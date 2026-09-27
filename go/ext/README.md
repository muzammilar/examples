# External Projects (Git Submodules)

Perform the commands in the root of the project to avoid unintended behaviour.
This link provides a more detailed answer: https://git.wiki.kernel.org/index.php/GitSubmoduleTutorial
## Adding a submodule
```sh
# mkdir
mkdir ext
# Select a directory and add the submodule
git submodule add https://github.com/muzammilar/geomrpc.git ext/geomrpc
# Add with a tag
git submodule add --branch 0.0.1 https://github.com/muzammilar/geomrpc.git ext/geomrpc

# Check submodule status (see `git submodule -h` for all commands)
git submodule status

```

## Updating a submodule
```sh
# Update using the path
git submodule update --remote ext/geomrpc

# Change branch or tag (and check status)
git submodule set-branch --branch master ext/geomrpc
git submodule update --remote ext/geomrpc
git submodule status

# Change branch to tag (and check status)
git submodule set-branch --branch 0.0.1 ext/geomrpc
git submodule update --remote ext/geomrpc
git submodule status
```

## Deteling a submodule

```sh
# Remove the submodule and update the .gitsubmodules file
git rm -r ext/geomrpc
# Commit your changes
# Remove the module data
rm -rf .git/modules/ext/geomrpc
# Update the .git/config
git config -f .git/config --remove-section "submodule.ext/geomrpc" 2> /dev/null

## Alternatively
# Remove config entries:
#git config -f .git/config --remove-section submodule.$submodulename
#git config -f .gitmodules --remove-section submodule.$submodulename

# Remove directory from index:
#git rm --cached $submodulepath

# Commit
#Delete unused files:
#rm -rf $submodulepath
#rm -rf .git/modules/$submodulename

# Please note: $submodulepath doesn't contain leading or trailing slashes.
```
