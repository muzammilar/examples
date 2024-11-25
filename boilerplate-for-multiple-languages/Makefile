
# certs
selfcert:
	mkdir -p /tmp/certs
# Generation of self-signed(x509) public key (PEM-encodings .pem|.crt) based on the private (.key)
	openssl req -x509 -newkey rsa:4096 -sha256 -nodes -keyout /tmp/certs/test-server.key -out /tmp/certs/test-server.crt -days 365 -subj "/C=US/ST=Denial/L=Springfield/O=Dis/CN=localhost" -addext "subjectAltName=DNS:localhost"
# allow all users to read the key file (since it's development)
# chmod 644 /tmp/certs/test-server.key
