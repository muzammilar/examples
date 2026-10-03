;; Guix equivalent of the flake.nix dev shell.
;; Pinned via channels.scm (the Guix analogue of flake.lock):
;;   guix time-machine -C channels.scm -- shell -m manifest.scm
;;
;; Not packaged in Guix; install into $HOME/go/bin (on PATH via .envrc):
;;   golangci-lint   go install github.com/golangci/golangci-lint/v2/cmd/golangci-lint@latest
;;   kind            go install sigs.k8s.io/kind@latest
;;   kubectl         release binary: https://kubernetes.io/docs/tasks/tools/install-kubectl-linux/
;;   helm            release binary: https://github.com/helm/helm/releases
;;   k9s             release binary: https://github.com/derailed/k9s/releases
;;   colorized-logs  (ansi2txt) build from https://github.com/kilobyte/colorized-logs
;; Docker is not provided: use Docker Engine on Linux.

(specifications->manifest
 (list
  ;; Go
  "go"
  "go-tools"     ; golang.org/x/tools commands (goimports, stringer, ...)

  ;; Valkey CLI (valkey-cli)
  "valkey"

  ;; Utilities
  "jq"
  "yq"           ; mikefarah/yq (nixpkgs yq-go)
  "gettext"      ; provides envsubst

  ;; LSPs
  "gopls"))
