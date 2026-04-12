# Sourceable helper — defines install_go_tools().
# Requires: $DOTFILES, $DRY_RUN

install_go_tools() {
  local hash
  hash=$(cd "$DOTFILES" && find tools -type f \( -name 'go.mod' -o \( -name '*.go' ! -name '*_test.go' \) \) \
      ! -path '*/testdata/*' | sort | xargs shasum | shasum | cut -d' ' -f1)
  hash=${hash:0:16}

  local stored_hash=""
  [ -f ~/.cache/dotfiles/go-build-hash ] && stored_hash=$(cat ~/.cache/dotfiles/go-build-hash)

  if [ "$hash" = "$stored_hash" ]; then
    echo "Go tools up to date"
    return 0
  fi

  local os arch
  os=$(uname -s | tr '[:upper:]' '[:lower:]')
  arch=$(uname -m)
  [ "$arch" = "aarch64" ] && arch="arm64"
  [ "$arch" = "x86_64" ] && arch="amd64"

  local binaries base_url tmpdir
  binaries=$(make -C "$DOTFILES/tools" -s list)
  base_url="https://github.com/rjernst/dotfiles/releases/download/go-build-$hash"
  tmpdir=$(mktemp -d)

  if (( DRY_RUN )); then
    rm -rf "$tmpdir"
    echo "Would install Go tools (hash: $hash)"
    return 0
  fi

  # Try downloading pre-built binaries
  local downloaded=true
  while IFS= read -r bin; do
    if ! curl -fLo "$tmpdir/$bin" "$base_url/${bin}-${os}-${arch}" 2>/dev/null; then
      downloaded=false
      break
    fi
  done <<< "$binaries"

  if [ "$downloaded" = true ]; then
    while IFS= read -r bin; do
      chmod +x "$tmpdir/$bin"
      mv "$tmpdir/$bin" "$HOME/bin/$bin"
    done <<< "$binaries"
  elif command -v go &>/dev/null; then
    echo "Pre-built binaries not available for hash $hash"
    read -rp "Build locally with Go? [y/N] " answer
    if [[ "$answer" =~ ^[Yy]$ ]]; then
      if ! make -C "$DOTFILES/tools" all; then
        rm -rf "$tmpdir"
        return 1
      fi
    else
      echo "Skipping Go tools"
      rm -rf "$tmpdir"
      return 1
    fi
  else
    rm -rf "$tmpdir"
    >&2 echo "WARNING: Pre-built binaries not available and Go not installed — Go tools not updated"
    return 1
  fi

  rm -rf "$tmpdir"
  mkdir -p ~/.cache/dotfiles
  echo "$hash" > ~/.cache/dotfiles/go-build-hash
}
