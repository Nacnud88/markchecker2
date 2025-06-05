#!/bin/bash

# deploy.sh - Deployment script for Voila Price Checker High Volume Edition
# For Ubuntu 24.10 x64 server with 1GB RAM / 25GB Disk

set -e

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${BLUE}[INFO]${NC} $1"; }
print_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Configuration
APP_NAME="voila-price-checker"
APP_DIR="/opt/${APP_NAME}"
APP_USER="www-data"
APP_GROUP="www-data"
PYTHON_VERSION="3.12"

print_status "🚀 Deploying Voila Price Checker High Volume Edition"
print_status "Target directory: $APP_DIR"
print_status "User: $APP_USER"

# Check if running as root
if [[ $EUID -ne 0 ]]; then
   print_error "This deployment script must be run as root (use sudo)"
   exit 1
fi

# Update system packages
print_status "Updating system packages..."
apt update
apt upgrade -y

# Install required system packages
print_status "Installing required system packages..."
apt install -y \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    build-essential \
    nginx \
    sqlite3 \
    htop \
    curl \
    git \
    supervisor \
    logrotate

print_success "System packages installed"

# Create application user if it doesn't exist
if ! id "$APP_USER" &>/dev/null; then
    print_status "Creating application user: $APP_USER"
    useradd --system --shell /bin/bash --home-dir $APP_DIR --create-home $APP_USER
else
    print_status "User $APP_USER already exists"
fi

# Create application directory
print_status "Setting up application directory..."
mkdir -p $APP_DIR
chown $APP_USER:$APP_GROUP $APP_DIR

# Copy application files
print_status "Copying application files..."
cp app.py $APP_DIR/
cp requirements.txt $APP_DIR/
cp gunicorn_config.py $APP_DIR/
cp run.sh $APP_DIR/
chmod +x $APP_DIR/run.sh

# Create templates directory and copy HTML file
mkdir -p $APP_DIR/templates
if [ -f "index.html" ]; then
    cp index.html $APP_DIR/templates/
elif [ -f "templates/index.html" ]; then
    cp templates/index.html $APP_DIR/templates/
else
    print_error "index.html not found!"
    exit 1
fi

# Create other necessary directories
mkdir -p $APP_DIR/{logs,temp,static}

# Set proper ownership
chown -R $APP_USER:$APP_GROUP $APP_DIR

# Switch to application user for Python setup
print_status "Setting up Python virtual environment..."
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
chown -R $APP_USER:$APP_GROUP venv

print_success "Python environment setup completed"

# Install systemd service
print_status "Installing systemd service..."
cat > /etc/systemd/system/${APP_NAME}.service << EOF
[Unit]
Description=Voila Price Checker High Volume Edition
Documentation=https://github.com/your-repo/voila-price-checker
After=network.target
Wants=network.target

[Service]
Type=exec
User=$APP_USER
Group=$APP_GROUP
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/gunicorn -c gunicorn_config.py app:app
ExecReload=/bin/kill -s HUP \$MAINPID

Restart=always
RestartSec=5
StartLimitInterval=60s
StartLimitBurst=3

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_DIR
PrivateTmp=true

LimitNOFILE=65536
LimitNPROC=1024

MemoryAccounting=true
MemoryMax=800M
MemoryHigh=700M

CPUAccounting=true
CPUQuota=400%
Nice=5

Environment="ENVIRONMENT=production"
Environment="PYTHONPATH=$APP_DIR"
Environment="PYTHONUNBUFFERED=1"

StandardOutput=journal
StandardError=journal
SyslogIdentifier=$APP_NAME

ExecStopPost=/bin/rm -f $APP_DIR/temp_products.db*
ExecStopPost=/bin/find $APP_DIR/temp -type f -mmin +5 -delete

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd and enable service
systemctl daemon-reload
systemctl enable $APP_NAME

print_success "Systemd service installed and enabled"

# Configure Nginx
print_status "Configuring Nginx..."
cat > /etc/nginx/sites-available/$APP_NAME << 'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 10M;
    client_body_timeout 300s;
    client_header_timeout 300s;
    send_timeout 300s;
    proxy_read_timeout 1800s;
    proxy_connect_timeout 300s;
    proxy_send_timeout 300s;

    # Gzip compression
    gzip on;
    gzip_vary on;
    gzip_min_length 1024;
    gzip_types
        text/plain
        text/css
        text/xml
        text/javascript
        application/json
        application/javascript
        application/xml+rss
        application/atom+xml
        image/svg+xml;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # WebSocket support (if needed in future)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Static files (if any)
    location /static/ {
        alias /opt/voila-price-checker/static/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Health check endpoint
    location /health {
        access_log off;
        return 200 "healthy\n";
        add_header Content-Type text/plain;
    }
}
EOF

# Enable Nginx site
ln -sf /etc/nginx/sites-available/$APP_NAME /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
nginx -t

print_success "Nginx configured"

# Configure log rotation
print_status "Setting up log rotation..."
cat > /etc/logrotate.d/$APP_NAME << EOF
$APP_DIR/logs/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
    copytruncate
    su $APP_USER $APP_GROUP
}
EOF

print_success "Log rotation configured"

# System optimizations
print_status "Applying system optimizations..."

# Increase file descriptor limits
echo "* soft nofile 65536" >> /etc/security/limits.conf
echo "* hard nofile 65536" >> /etc/security/limits.conf

# Optimize SQLite performance
echo "# SQLite optimizations for Voila Price Checker" >> /etc/sysctl.conf
echo "vm.dirty_background_ratio = 5" >> /etc/sysctl.conf
echo "vm.dirty_ratio = 10" >> /etc/sysctl.conf
echo "vm.dirty_expire_centisecs = 1200" >> /etc/sysctl.conf
echo "vm.dirty_writeback_centisecs = 500" >> /etc/sysctl.conf

# Apply sysctl changes
sysctl -p

print_success "System optimizations applied"

# Create a monitoring script
print_status "Creating monitoring script..."
cat > $APP_DIR/monitor.sh << 'EOF'
#!/bin/bash
# Simple monitoring script for Voila Price Checker

APP_NAME="voila-price-checker"
LOG_FILE="/var/log/voila-monitor.log"

log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> $LOG_FILE
}

# Check if service is running
if ! systemctl is-active --quiet $APP_NAME; then
    log_message "ERROR: $APP_NAME service is not running. Attempting restart..."
    systemctl restart $APP_NAME
    sleep 10
    if systemctl is-active --quiet $APP_NAME; then
        log_message "SUCCESS: $APP_NAME service restarted successfully"
    else
        log_message "ERROR: Failed to restart $APP_NAME service"
    fi
fi

# Check memory usage
MEMORY_USAGE=$(ps aux | grep -E '(gunicorn|python).*app:app' | grep -v grep | awk '{sum+=$6} END {print sum/1024}')
if (( $(echo "$MEMORY_USAGE > 800" | bc -l) )); then
    log_message "WARNING: High memory usage detected: ${MEMORY_USAGE}MB"
fi

# Check disk space
DISK_USAGE=$(df /opt/voila-price-checker | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -gt 80 ]; then
    log_message "WARNING: High disk usage: ${DISK_USAGE}%"
    # Clean up old database files
    find /opt/voila-price-checker -name "temp_products.db*" -mmin +60 -delete
fi

# Check for database locks
if [ -f "/opt/voila-price-checker/temp_products.db-wal" ]; then
    WAL_SIZE=$(stat -c%s "/opt/voila-price-checker/temp_products.db-wal")
    if [ "$WAL_SIZE" -gt 10485760 ]; then  # 10MB
        log_message "WARNING: Large WAL file detected: ${WAL_SIZE} bytes"
    fi
fi
EOF

chmod +x $APP_DIR/monitor.sh
chown $APP_USER:$APP_GROUP $APP_DIR/monitor.sh

# Add monitoring to crontab
(crontab -u $APP_USER -l 2>/dev/null; echo "*/5 * * * * $APP_DIR/monitor.sh") | crontab -u $APP_USER -

print_success "Monitoring script installed"

# Test the installation (skip if run.sh doesn't have test function)
print_status "Testing installation..."
if [ -f "$APP_DIR/run.sh" ] && grep -q "test" "$APP_DIR/run.sh"; then
    su -c "cd $APP_DIR && ./run.sh test" $APP_USER
    print_success "Installation test completed"
else
    print_warning "Skipping test - run.sh test function not found"
fi

# Start services
print_status "Starting services..."
systemctl restart nginx
systemctl start $APP_NAME

# Wait a moment for services to start
sleep 5

# Check service status
if systemctl is-active --quiet $APP_NAME; then
    print_success "$APP_NAME service is running"
else
    print_error "$APP_NAME service failed to start"
    systemctl status $APP_NAME
    exit 1
fi

if systemctl is-active --quiet nginx; then
    print_success "Nginx service is running"
else
    print_error "Nginx service failed to start"
    systemctl status nginx
    exit 1
fi

# Display final information
print_success "🎉 Deployment completed successfully!"
echo ""
echo "Application Details:"
echo "  • Service: $APP_NAME"
echo "  • Directory: $APP_DIR"
echo "  • User: $APP_USER"
echo "  • URL: http://$(hostname -I | awk '{print $1}')"
echo ""
echo "Useful Commands:"
echo "  • Check status: sudo systemctl status $APP_NAME"
echo "  • View logs: sudo journalctl -u $APP_NAME -f"
echo "  • Restart service: sudo systemctl restart $APP_NAME"
echo "  • Monitor resources: htop"
echo "  • Check nginx: sudo nginx -t"
echo ""
echo "Files and Directories:"
echo "  • Application: $APP_DIR"
echo "  • Logs: $APP_DIR/logs"
echo "  • Database: $APP_DIR/temp_products.db (temporary)"
echo "  • Monitoring: $APP_DIR/monitor.sh"
echo ""
print_warning "Remember to:"
print_warning "1. Configure your firewall to allow HTTP traffic (port 80)"
print_warning "2. Set up SSL/TLS certificate for production use"
print_warning "3. Monitor the application logs regularly"
print_warning "4. The temporary database files are cleaned up automatically"
