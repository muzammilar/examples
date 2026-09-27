## URL Unshorten

```sh
# Use UV
curl -LsSf https://astral.sh/uv/install.sh | sh
# uv init .
# rm main.py
# uv add requests
# uv add beautifulsoup4
uv sync

# run the script
uv run unshorten_url_linkedin.py -h
uv run unshorten_url_linkedin.py -i ./interviews/0-preparation-materials/0.md
uv run unshorten_url_linkedin.py -i ./interviews/0-preparation-materials/0.md --replace
uv run unshorten_url_linkedin.py -i ./interviews/0-preparation-materials/0.md -o ./interviews/0-preparation-materials/0.md

```
