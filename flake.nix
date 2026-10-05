{
  description = "Examples dev shell (Go, kind, kubectl, helm, valkey-cli)";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    # nixpkgs dropped x86_64-darwin (Intel Macs) in 26.11
    flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" ] (system:
      let
        pkgs = import nixpkgs { inherit system; };
      in {
        devShells.default = pkgs.mkShell {
          nativeBuildInputs = with pkgs; [
            # Go
            go
            golangci-lint
            gotools

            # Kubernetes (kind clusters, operators, Helm charts)
            kind
            kubectl
            kubernetes-helm
            k9s

            # Valkey CLI (valkey-cli)
            valkey

            # Utilities
            jq
            yq-go
            gettext # provides envsubst
            colorized-logs # provides ansi2txt

            # LSPs
            gopls
          ];

          # Docker is not provided: use Docker Desktop, or Docker Engine on Linux.
          shellHook = ''
            # go install puts binaries in $HOME/go/bin
            export PATH="$PATH:$HOME/go/bin"

            # Load local overrides
            if [ -f .env ]; then
              set -a; . ./.env; set +a
            fi
          '';
        };
      });
}
