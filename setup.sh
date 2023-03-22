#!/usr/bin/env bash

OS=$(lsb_release -i)
OS_NAME=$(cut -f2 <<< "$OS")

RELEASE=$(lsb_release -r)
OS_RELEASE=$(cut -f2 <<< "$RELEASE")

if [ "Ubuntu" != "$OS" ] && [ "22.04" != "$OS_RELEASE" ]; then
  echo "Only Ubuntu 22.04 is supported"
  exit
fi

# Remove old / Other docker and snapd
REMOVE_PKGS="docker docker-engine docker.io containerd runc snapd"
sudo apt-get remove -y $REMOVE_PKGS
sudo apt-get purge -y $REMOVE_PKGS

# Install podman
sudo apt-get update
sudo apt-get install podman
sudo apt-get install python3-docker podman-docker # Docker API (podman do not have a API yet)
sudo apt-get install python3-ldap
sudo apt-get install freeipa-client

# Install other dependencies
REQUIRED_PKGS="iptables python3"
for p in $REQUIRED_PKGS
do
  PKG_OK=$(dpkg-query -W --showformat='${Status}\n' $p|grep "install ok installed")
  echo "Checking for $p: $PKG_OK"
  if [ "" = "$PKG_OK" ]; then
    echo "Installing $p"
    sudo apt-get --yes install $p
  fi
done

# Check if machine already have been joined into a domain
FILE=/etc/krb5.keytab
if [ ! -f "$FILE" ]; then
  IPA_DOMAIN=domain.internal
  IPA_SERVER="ipa.$IPA_DOMAIN"

  echo ""
  echo "[#] Please provide login info for Freeipa:"
  #read -p 'LDAP Server(FreeIPA) (ipa.domain.internal): ' IPA_SERVER
  #read -p 'LDAP Domain (domain.internal): ' IPA_DOMAIN
  read -p 'Username: ' PRINCIPAL

  # Join domain
  #sudo -i echo ipa-client-install --principal=$PRINCIPAL -W --server=$IPA_SERVER --domain=$IPA_DOMAIN --no-ntp --no-ssh --no-sshd --mkhomedir
  sudo -i $(echo ipa-client-install --principal=$PRINCIPAL -W --server=$IPA_SERVER --domain=$IPA_DOMAIN --no-ntp --no-ssh --no-sshd --mkhomedir --force-join)
fi

# Create folder and add the program
sudo mkdir -p /opt/homefolders
sudo mkdir -p /opt/keytabs
sudo mkdir -p /usr/bin/divisora
sudo cp node-manager.py /usr/bin/divisora/node-manager.py

# Create service and start it
sudo cp divisora-node-manager.service /etc/systemd/system/divisora-node-manager.service
sudo chmod 644 /etc/systemd/system/divisora-node-manager.service

# Ask for information and update divisora-node-manager.service
echo ""
echo "[#] Please provide the script with some information:"
read -p 'Core-Manager Address (127.0.0.1): ' coremgr
read -p 'Source Address (10.0.0.1): ' srcaddr
read -p 'LDAP Server(FreeIPA) (ldap://ipa.domain.internal:389): ' ldapsrv

sed -i "s/-s PLACEHOLDER/-s $coremgr/" /etc/systemd/system/divisora-node-manager.service
sed -i "s/--src PLACEHOLDER/--src $srcaddr/" /etc/systemd/system/divisora-node-manager.service
# | is not a typo. Since FQDN will contain /, this char cannot be used as a seperator
sed -i "s|--ldap PLACEHOLDER|--ldap $ldapsrv|" /etc/systemd/system/divisora-node-manager.service

sudo systemctl daemon-reload
sudo systemctl enable divisora-node-manager.service
sudo systemctl start divisora-node-manager.service