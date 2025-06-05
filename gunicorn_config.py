# gunicorn_config.py
# Optimized for 1GB RAM environment with high-volume processing
import multiprocessing
import os

# Server socket settings
bind = "0.0.0.0:5000"

# Worker settings - optimized for 1GB RAM with chunk processing
workers = 3  # Increased from 2 since we have more memory and chunk processing
worker_class = "sync"  # Using sync worker for better memory management
threads = 2  # Keep threads low to prevent memory bloat

# Worker timeout settings - increased for large dataset processing
timeout = 1800  # 30 minutes for very large requests (40k+ articles)
graceful_timeout = 120  # 2 minutes graceful shutdown
keepalive = 120  # Keep connections alive longer for chunk processing

# Restart workers to prevent memory leaks - more aggressive for large processing
max_requests = 100  # Restart workers after fewer requests to manage memory
max_requests_jitter = 25  # Add randomness to avoid all workers restarting at once

# Memory management
worker_tmp_dir = "/dev/shm"  # Use shared memory for temp files
limit_request_line = 8192  # Increased for larger search requests
limit_request_fields = 200  # More fields for complex requests
limit_request_field_size = 16384  # Larger field sizes for bulk data

# Logging - balanced verbosity for monitoring large operations
loglevel = "info"  # More verbose than warning to monitor chunk processing
accesslog = "-"  # Log to stdout
errorlog = "-"   # Log errors to stdout
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "voila_price_checker_hv"  # HV for High Volume

# Memory optimization settings
preload_app = False  # Don't preload to save startup memory

# Worker recycling settings
worker_connections = 1000

def post_fork(server, worker):
    """Called after worker fork to optimize memory"""
    import gc
    import sqlite3
    
    # Force garbage collection after fork
    gc.collect()
    
    # Initialize SQLite settings for this worker
    # Enable WAL mode for better concurrent access
    try:
        conn = sqlite3.connect('temp_products.db')
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        conn.execute('PRAGMA cache_size=10000')  # 10MB cache per worker
        conn.execute('PRAGMA temp_store=MEMORY')
        conn.close()
    except Exception as e:
        print(f"Warning: Could not optimize SQLite for worker {worker.pid}: {e}")

def worker_int(worker):
    """Called when worker receives SIGINT"""
    import gc
    import os
    
    # Run garbage collection on worker shutdown
    gc.collect()
    
    # Clean up any temp files
    try:
        if os.path.exists('temp_products.db-wal'):
            os.remove('temp_products.db-wal')
        if os.path.exists('temp_products.db-shm'):
            os.remove('temp_products.db-shm')
    except:
        pass

def on_exit(server):
    """Called when master process exits"""
    import os
    import sqlite3
    
    # Clean up database files
    try:
        # Close any remaining connections
        conn = sqlite3.connect('temp_products.db')
        conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        conn.close()
        
        # Remove database files
        for db_file in ['temp_products.db', 'temp_products.db-wal', 'temp_products.db-shm']:
            if os.path.exists(db_file):
                os.remove(db_file)
                print(f"Cleaned up {db_file}")
    except Exception as e:
        print(f"Warning: Could not clean up database files: {e}")

# Memory monitoring settings
max_requests_jitter = 25

# Environment-specific settings
if os.environ.get('ENVIRONMENT') == 'production':
    # Production settings - more conservative
    workers = 2
    max_requests = 50
    timeout = 900  # 15 minutes
elif os.environ.get('ENVIRONMENT') == 'development':
    # Development settings - more verbose logging
    loglevel = "debug"
    reload = True
    workers = 1
