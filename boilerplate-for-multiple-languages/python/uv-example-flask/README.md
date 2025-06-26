
```sh
# create
uv init uv-example-flask
# example directory
cd uv-example-flask
# run basic example
uv run main.py
# add dependencies
uv add flask
uv add pytest
# add some tests/code
mkdir -p tests
mkdir -p app
touch app/__init__.py
# write the code
# run tests
uv run pytest
uv run app/routes.py
```
