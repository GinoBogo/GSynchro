#!/usr/bin/env python3
"""
SSH connection manager for GSynchro.

Manages SSH connections with connection pooling to improve performance
when working with remote file systems.

 Author: Gino Bogo
License: MIT
Version: 1.0
"""

# Standard library imports.
import threading
from contextlib import contextmanager
from queue import Queue

# Third-party library imports.
import paramiko


class ConnectionManager:
    """Manages SSH connections with pooling."""

    def __init__(self, logger_func, pool_size=4):
        """Initialize connection manager with logger and pool size."""
        self._pools = {}
        self._pool_configs = {}
        self._lock = threading.Lock()
        self.log = logger_func
        self.pool_size = pool_size

    def _get_server_key(self, host, user, port):
        """Generate unique server key for connection pooling."""
        return f"{user}@{host}:{port}"

    def _create_connection(self, host, user, password, port):
        """Create a new SSH connection to the specified server."""
        self.log(f"Creating new SSH connection for {user}@{host}:{port}")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, username=user, password=password, port=port)
        return client

    def _initialize_pool(self, server_key, host, user, password, port):
        """Initialize connection pool for a server with multiple connections."""
        if server_key not in self._pools:
            self._pools[server_key] = Queue()
            self._pool_configs[server_key] = (host, user, password, port)
            for i in range(self.pool_size):
                try:
                    conn = self._create_connection(host, user, password, port)
                    self._pools[server_key].put(conn)
                except Exception as e:
                    self.log(
                        f"SSH connection {i + 1}/{self.pool_size} failed for {server_key}: {e}"
                    )

    @contextmanager
    def get_connection(self, host, user, password, port):
        """Context manager to get a connection from the pool, creating if needed."""
        server_key = self._get_server_key(host, user, port)
        with self._lock:
            if server_key not in self._pools:
                self._initialize_pool(server_key, host, user, password, port)

            conn = None
            try:
                conn = self._pools[server_key].get(timeout=10)
                transport = conn.get_transport() if conn else None
                if not transport or not transport.is_active():
                    self.log(f"Connection for {server_key} is dead, creating new one")
                    conn = self._create_connection(host, user, password, port)
                yield conn
            except Exception as e:
                self.log(f"Error getting connection for {server_key}: {e}")
                conn = self._create_connection(host, user, password, port)
                yield conn
            finally:
                if conn and server_key in self._pools:
                    try:
                        transport = conn.get_transport()
                        if transport and transport.is_active():
                            self._pools[server_key].put(conn, timeout=1)
                        else:
                            conn.close()
                            host, user, password, port = self._pool_configs[server_key]
                            new_conn = self._create_connection(
                                host, user, password, port
                            )
                            self._pools[server_key].put(new_conn, timeout=1)
                    except Exception:
                        try:
                            conn.close()
                        except Exception:
                            pass

    def close_all(self):
        """Close all SSH connections in all pools."""
        with self._lock:
            for server_key, pool in self._pools.items():
                self.log(f"Closing SSH pool {server_key}")
                while not pool.empty():
                    try:
                        conn = pool.get_nowait()
                        if conn:
                            conn.close()
                    except Exception:
                        pass
            self._pools.clear()
            self._pool_configs.clear()
