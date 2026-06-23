#!/bin/bash
# Deploy hOn integration to Home Assistant
# Usage: ./deploy.sh
# Requires: HA_HOST set or defaults to 192.168.0.2

set -e

LOCAL_COMPONENT="custom_components/hon"
REMOTE_COMPONENT_PATH="/config/custom_components/"
HA_PORT=22
HA_SSH=davidromero@192.168.0.2

# Step 1: deploy the custom component
rsync -avz \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    -e "ssh -p $HA_PORT" \
    "$LOCAL_COMPONENT/" \
    "$HA_SSH:$REMOTE_COMPONENT_PATH/hon/"

# Step 2: patch the installed pyhon package inside the HA container
# Haier retired the Salesforce login in 2026-06; these files replace the broken ones.
PYHON_PATCH="custom_components/hon/pyhon_patch"
REMOTE_TMP="/tmp/pyhon_patch"

echo "Uploading pyhon patches..."
rsync -avz \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    -e "ssh -p $HA_PORT" \
    "$PYHON_PATCH/" \
    "$HA_SSH:$REMOTE_TMP/"

echo "Applying pyhon patches inside HA container..."
ssh -p $HA_PORT $HA_SSH "
    PYHON_DIR=\$(find /usr/local/lib -name 'hon.py' -path '*/pyhon/connection/*' 2>/dev/null | head -1 | xargs dirname 2>/dev/null)
    if [ -z \"\$PYHON_DIR\" ]; then
        echo 'ERROR: pyhon not found in /usr/local/lib'
        exit 1
    fi
    PYHON_ROOT=\$(dirname \"\$PYHON_DIR\")
    echo \"pyhon found at \$PYHON_ROOT\"
    cp $REMOTE_TMP/connection/auth.py \"\$PYHON_DIR/auth.py\"
    cp $REMOTE_TMP/connection/api.py \"\$PYHON_ROOT/connection/api.py\"
    cp $REMOTE_TMP/connection/hon.py \"\$PYHON_DIR/hon.py\"
    cp $REMOTE_TMP/const.py \"\$PYHON_ROOT/const.py\"
    cp $REMOTE_TMP/helper.py \"\$PYHON_ROOT/helper.py\"
    echo 'pyhon patched successfully'
    find \"\$PYHON_ROOT\" -name '__pycache__' -type d | xargs rm -rf
    echo 'pycache cleared'
"
