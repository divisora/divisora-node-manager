## Divisora: Private, Automatic and Dynamic portal to other security zones
## Description
T.B.D

## Prerequisites
- FreeIPA
- Divisora-Core-Manager

## Build / Run
```
./setup.sh
```

## Freeipa setup
```
hostnamectl hostname node-1.domain.internal

# Join the node into the realm/domain
ipa-client-install -p admin -W -f --mkhomedir --force-join --server=ipa.domain.internal --domain=domain.internal --no-ntp

kinit admin
# Add Node into nodes group
ipa hostgroup-add --desc="Nodes for cubicles" nodes
ipa hostgroup-add-member --hosts=node-1.domain.internal nodes

# Add cubicle
ipa host-add cubicle-user1-ubuntu.domain.internal --force
ipa host-allow-create-keytab cubicle-user1-ubuntu.domain.internal --hostgroups=nodes
ipa host-allow-retrieve-keytab cubicle-user1-ubuntu.domain.internal --hostgroups=nodes

# Retrieve keytabs with node-1 keytab
kdestroy
kinit -k -t /etc/krb5.keytab
ipa-getkeytab -v -Y GSSAPI -s ipa.domain.internal -p host/cubicle-user1-ubuntu.domain.internal -k /opt/keytabs/cubicle-user1-ubuntu.domain.internal.keytab

# Add service
ipa service-add HTTP/node-1.domain.internal@DOMAIN.INTERNAL
ipa-getcert request -f /opt/certs/node-1.domain.internal.crt -k /opt/certs/node-1.domain.internal.key -K HTTP/node-1.domain.internal@DOMAIN.INTERNAL -D node-1.domain.internal

# Mount /opt/keytabs/cubicle-user1-ubuntu.domain.internal.keytab -> /etc/krb5.keytab
```