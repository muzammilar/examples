# Koanf Example

A basic example of using kaonf with overrides using different prescendences. See [koanf repo](https://github.com/knadh/koanf) for details.
In this example, base config is loaded (yaml), then the override file is applied (json), then the final override is applied using environment variables.

```sh
docker compose up --build

```

#### Docker Environment Variables and Docker Compose

```sh
# Compose Example: https://docs.docker.com/compose/compose-application-model/
# Env variable prescedence: https://docs.docker.com/compose/environment-variables/envvars-precedence/
# Env variable substitutions and interpolations: https://docs.docker.com/compose/environment-variables/env-file/
```
