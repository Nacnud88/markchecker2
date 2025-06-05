# config.py - Configuration settings for Voila Price Checker High Volume Edition

import os

class Config:
    """Base configuration class"""
    
    # Database settings
    DB_PATH = os.environ.get('DB_PATH', 'temp_products.db')
    SESSION_CLEANUP_HOURS = int(os.environ.get('SESSION_CLEANUP_HOURS', '24'))
    
    # Processing settings
    CHUNK_SIZE = int(os.environ.get('CHUNK_SIZE', '500'))  # Articles per chunk
    MAX_WORKERS = int(os.environ.get('MAX_WORKERS', '3'))  # Concurrent API requests
    REQUEST_TIMEOUT = int(os.environ.get('REQUEST_TIMEOUT', '15'))  # API timeout in seconds
    
    # Memory management
    GC_ENABLED = os.environ.get('GC_ENABLED', 'True').lower() == 'true'
    
    # Rate limiting (to be respectful to Voila's API)
    MIN_REQUEST_INTERVAL = float(os.environ.get('MIN_REQUEST_INTERVAL', '0.1'))  # Seconds between requests
    MAX_REQUESTS_PER_MINUTE = int(os.environ.get('MAX_REQUESTS_PER_MINUTE', '200'))
    
    # Application settings
    SECRET_KEY = os.environ.get('SECRET_KEY', 'your-secret-key-change-this')
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    
    # Logging
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    
class DevelopmentConfig(Config):
    """Development configuration"""
    DEBUG = True
    CHUNK_SIZE = 50  # Smaller chunks for testing
    MAX_WORKERS = 2  # Fewer workers for development
    SESSION_CLEANUP_HOURS = 1  # Cleanup more frequently in development
    LOG_LEVEL = 'DEBUG'

class ProductionConfig(Config):
    """Production configuration"""
    DEBUG = False
    CHUNK_SIZE = 500  # Optimal chunk size for production
    MAX_WORKERS = 3  # Balanced for 1GB RAM
    SESSION_CLEANUP_HOURS = 24
    LOG_LEVEL = 'INFO'

class HighVolumeConfig(Config):
    """Configuration for very large datasets (20k+ articles)"""
    DEBUG = False
    CHUNK_SIZE = 300  # Smaller chunks to manage memory
    MAX_WORKERS = 2  # Fewer workers to preserve memory
    REQUEST_TIMEOUT = 30  # Longer timeout for stability
    SESSION_CLEANUP_HOURS = 6  # More frequent cleanup
    LOG_LEVEL = 'INFO'

class LowMemoryConfig(Config):
    """Configuration for low memory environments (512MB RAM)"""
    DEBUG = False
    CHUNK_SIZE = 100  # Very small chunks
    MAX_WORKERS = 1  # Single worker to minimize memory usage
    REQUEST_TIMEOUT = 20
    SESSION_CLEANUP_HOURS = 2
    GC_ENABLED = True
    LOG_LEVEL = 'WARNING'

# Configuration mapping
config_map = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'high_volume': HighVolumeConfig,
    'low_memory': LowMemoryConfig
}

def get_config(config_name=None):
    """Get configuration based on environment variable or parameter"""
    if config_name is None:
        config_name = os.environ.get('ENVIRONMENT', 'production')
    
    return config_map.get(config_name, ProductionConfig)

# Performance profiles for different server specifications
PERFORMANCE_PROFILES = {
    'minimal': {
        # For 512MB RAM servers
        'CHUNK_SIZE': 100,
        'MAX_WORKERS': 1,
        'REQUEST_TIMEOUT': 20,
        'description': 'Minimal profile for 512MB RAM servers'
    },
    'standard': {
        # For 1GB RAM servers (default)
        'CHUNK_SIZE': 500,
        'MAX_WORKERS': 3,
        'REQUEST_TIMEOUT': 15,
        'description': 'Standard profile for 1GB RAM servers'
    },
    'enhanced': {
        # For 2GB+ RAM servers
        'CHUNK_SIZE': 1000,
        'MAX_WORKERS': 5,
        'REQUEST_TIMEOUT': 15,
        'description': 'Enhanced profile for 2GB+ RAM servers'
    },
    'high_performance': {
        # For 4GB+ RAM servers
        'CHUNK_SIZE': 2000,
        'MAX_WORKERS': 8,
        'REQUEST_TIMEOUT': 10,
        'description': 'High performance profile for 4GB+ RAM servers'
    }
}

def apply_performance_profile(profile_name):
    """Apply a performance profile by setting environment variables"""
    if profile_name not in PERFORMANCE_PROFILES:
        raise ValueError(f"Unknown profile: {profile_name}")
    
    profile = PERFORMANCE_PROFILES[profile_name]
    
    for key, value in profile.items():
        if key != 'description':
            os.environ[key] = str(value)
    
    return profile

# Auto-detect optimal settings based on available memory
def auto_configure():
    """Automatically configure based on available system memory"""
    try:
        import psutil
        available_memory_gb = psutil.virtual_memory().available / (1024 ** 3)
        
        if available_memory_gb < 0.7:  # Less than 700MB available
            return apply_performance_profile('minimal')
        elif available_memory_gb < 1.5:  # Less than 1.5GB available
            return apply_performance_profile('standard')
        elif available_memory_gb < 3.0:  # Less than 3GB available
            return apply_performance_profile('enhanced')
        else:  # 3GB+ available
            return apply_performance_profile('high_performance')
            
    except ImportError:
        # psutil not available, use standard profile
        return apply_performance_profile('standard')
    except Exception as e:
        print(f"Error in auto-configuration: {e}")
        return apply_performance_profile('standard')

# Validation functions
def validate_config(config):
    """Validate configuration settings"""
    errors = []
    
    if config.CHUNK_SIZE < 10 or config.CHUNK_SIZE > 5000:
        errors.append("CHUNK_SIZE must be between 10 and 5000")
    
    if config.MAX_WORKERS < 1 or config.MAX_WORKERS > 20:
        errors.append("MAX_WORKERS must be between 1 and 20")
    
    if config.REQUEST_TIMEOUT < 5 or config.REQUEST_TIMEOUT > 300:
        errors.append("REQUEST_TIMEOUT must be between 5 and 300 seconds")
    
    if config.SESSION_CLEANUP_HOURS < 1 or config.SESSION_CLEANUP_HOURS > 168:
        errors.append("SESSION_CLEANUP_HOURS must be between 1 and 168 hours")
    
    return errors

# Performance estimation
def estimate_performance(article_count, config):
    """Estimate processing time based on configuration and article count"""
    chunks_needed = (article_count + config.CHUNK_SIZE - 1) // config.CHUNK_SIZE
    
    # Rough estimates based on observed performance
    avg_chunk_time = 30 + (config.CHUNK_SIZE * 0.1)  # Base time + time per article
    parallel_factor = min(config.MAX_WORKERS, chunks_needed) / chunks_needed if chunks_needed > 0 else 1
    
    estimated_seconds = (chunks_needed * avg_chunk_time) * parallel_factor
    estimated_minutes = estimated_seconds / 60
    
    return {
        'chunks_needed': chunks_needed,
        'estimated_seconds': int(estimated_seconds),
        'estimated_minutes': round(estimated_minutes, 1),
        'estimated_memory_mb': (article_count * 0.05) + 100,  # Rough memory estimate
        'parallel_factor': parallel_factor
    }

# Configuration summary
def print_config_summary(config):
    """Print a summary of the current configuration"""
    print("\n" + "="*50)
    print("VOILA PRICE CHECKER CONFIGURATION")
    print("="*50)
    print(f"Environment: {os.environ.get('ENVIRONMENT', 'production')}")
    print(f"Chunk Size: {config.CHUNK_SIZE} articles per chunk")
    print(f"Max Workers: {config.MAX_WORKERS} concurrent requests")
    print(f"Request Timeout: {config.REQUEST_TIMEOUT} seconds")
    print(f"Database Path: {config.DB_PATH}")
    print(f"Session Cleanup: {config.SESSION_CLEANUP_HOURS} hours")
    print(f"Garbage Collection: {'Enabled' if config.GC_ENABLED else 'Disabled'}")
    print(f"Debug Mode: {'Enabled' if config.DEBUG else 'Disabled'}")
    print(f"Log Level: {config.LOG_LEVEL}")
    print("="*50)
    
    # Show performance estimates for common dataset sizes
    test_sizes = [100, 1000, 10000, 40000]
    print("\nPERFORMANCE ESTIMATES:")
    print("-" * 50)
    for size in test_sizes:
        estimate = estimate_performance(size, config)
        print(f"{size:>6} articles: ~{estimate['estimated_minutes']:>5.1f} min, "
              f"~{estimate['estimated_memory_mb']:>4.0f}MB, "
              f"{estimate['chunks_needed']:>3} chunks")
    print("="*50 + "\n")

