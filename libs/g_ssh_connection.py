#!/usr/bin/env python3
"""
SSH connection manager for GSynchro.

Manages SSH connections with connection pooling to improve performance
when working with remote file systems.

 Author: Gino Bogo
License: MIT
Version: 1.1
"""

# Standard library imports.
import threading
from contextlib import contextmanager
from queue import Queue, Empty

# Third-party library imports.
import paramiko


class ConnectionManager:
    """Manages SSH connections with pooling."""

    def __init__(self, logger_func, pool_size=4, connect_timeout=30):
        """
        Initialize connection manager with logger and pool size.

        Args:
            logger_func (callable): Function to use for logging messages.
            pool_size (int): Number of connections to maintain in each pool.
            connect_timeout (int): Timeout in seconds for SSH connection operations.
        """
        self._pools = {}
        self._pool_configs = {}
        self._pool_locks = {}  # Per-server lock for pool operations
        self._global_lock = threading.Lock()
        self.log = logger_func
        self.pool_size = pool_size
        self.connect_timeout = connect_timeout

    def _get_server_key(self, host, user, port):
        """
        Generate unique server key for connection pooling.

        Args:
            host (str): Hostname or IP address of the SSH server.
            user (str): Username for SSH authentication.
            port (int): Port number for SSH connection.

        Returns:
            str: Unique identifier for the server connection.
        """
        return f"{user}@{host}:{port}"

    def _create_connection(self, host, user, password, port):
        """
        Create a new SSH connection to the specified server.

        Args:
            host (str): Hostname or IP address of the SSH server.
            user (str): Username for SSH authentication.
            password (str): Password for SSH authentication.
            port (int): Port number for SSH connection.

        Returns:
            paramiko.SSHClient: Connected SSH client instance.

        Raises:
            Exception: If connection fails for any reason.
        """
        self.log(f"Creating new SSH connection for {user}@{host}:{port}")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            host,
            username=user,
            password=password,
            port=port,
            timeout=self.connect_timeout,
            banner_timeout=self.connect_timeout,
            auth_timeout=self.connect_timeout,
        )
        return client

    def _is_connection_alive(self, conn):
        """Check if a connection's transport is active."""
        try:
            transport = conn.get_transport()
            return transport is not None and transport.is_active()
        except Exception:
            return False

    def _initialize_pool(self, server_key, host, user, password, port):
        """
        Initialize connection pool for a server with multiple connections.

        Args:
            server_key (str): Unique identifier for the server connection.
            host (str): Hostname or IP address of the SSH server.
            user (str): Username for SSH authentication.
            password (str): Password for SSH authentication.
            port (int): Port number for SSH connection.
        """
        if server_key not in self._pools:
            self._pools[server_key] = Queue(maxsize=self.pool_size)
            self._pool_configs[server_key] = (host, user, password, port)
            self._pool_locks[server_key] = threading.Lock()
            failed = 0
            for i in range(self.pool_size):
                try:
                    conn = self._create_connection(host, user, password, port)
                    self._pools[server_key].put_nowait(conn)
                except Exception as e:
                    self.log(
                        f"SSH connection {i + 1}/{self.pool_size} failed for {server_key}: {e}"
                    )
                    failed += 1
            if failed == self.pool_size:
                self.log(f"WARNING: All connections failed for {server_key}")

    @contextmanager
    def get_connection(self, host, user, password, port):
        """
        Context manager to get a connection from the pool, creating if needed.

        This method provides a thread-safe way to acquire an SSH connection
        from the pool. If no connections are available, it will block until
        one becomes available or create a new one if necessary.

        Args:
            host (str): Hostname or IP address of the SSH server.
            user (str): Username for SSH authentication.
            password (str): Password for SSH authentication.
            port (int): Port number for SSH connection.

        Yields:
            paramiko.SSHClient: Connected SSH client instance.

        Raises:
            Exception: If unable to establish a connection.
        """
        server_key = self._get_server_key(host, user, port)

        # Lazy initialization under global lock (fast, only happens once per server)
        with self._global_lock:
            if server_key not in self._pools:
                self._initialize_pool(server_key, host, user, password, port)
            pool = self._pools[server_key]
            pool_lock = self._pool_locks[server_key]
            pool_config = self._pool_configs[server_key]

        conn = None
        try:
            # Acquire per-server lock, get connection, validate, then release lock
            with pool_lock:
                try:
                    conn = pool.get(timeout=10)
                except Empty:
                    conn = None

                if conn is not None and not self._is_connection_alive(conn):
                    self.log(f"Connection for {server_key} is dead, creating new one")
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None

                if conn is None:
                    conn = self._create_connection(*pool_config)

            yield conn

        except Exception as e:
            self.log(f"Error getting connection for {server_key}: {e}")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            raise

        finally:
            # Return connection to pool (outside any lock to avoid blocking)
            if conn is not None and server_key in self._pools:
                try:
                    if self._is_connection_alive(conn):
                        # put_nowait on bounded queue: if full, close and drop
                        self._pools[server_key].put_nowait(conn)
                    else:
                        conn.close()
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass

    def close_all(self):
        """
        Close all SSH connections in all pools.

        This method should be called when the connection manager is no longer
        needed to ensure all connections are properly closed and resources
        are released.
        """
        with self._global_lock:
            for server_key, pool in list(self._pools.items()):
                self.log(f"Closing SSH pool {server_key}")
                while True:
                    try:
                        conn = pool.get_nowait()
                        if conn:
                            conn.close()
                    except Empty:
                        break
                    except Exception:
                        pass
            self._pools.clear()
            self._pool_configs.clear()
            self._pool_locks.clear()
