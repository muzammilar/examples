## Ticket Burner

A minimalistic, half-baked, simple application to create software tickets and assign owners, similar to JIRA. This is not production grade.

### Development

```sh
# connect to vagrant
vagrant up
vagrant ssh

# update uv requirements
cd ~/tktburn
uv sync # sync if needed

# remove database if you need to
rm webapp/example.db

# run application and listen on all hosts - make dev
cd webapp
uv run fastapi --verbose dev --host 0.0.0.0

# alternatively - make run
uv run fastapi webapp/main.py

# Run manual tests
curl -X POST http://localhost:8000/users -H 'Content-Type: application/json' -d '{"name": "john"}'
curl -X GET http://localhost:8000/users
```
