# Arch Linux package

`PKGBUILD` builds the release tarball of a `v<pkgver>` tag into an `any` package.

```bash
cd packaging/arch && makepkg -si
```

## Releasing a new version
1. Tag and release `v<new>` on GitHub.
2. Bump `pkgver` (reset `pkgrel=1`) in `PKGBUILD`.
3. `updpkgsums && makepkg --printsrcinfo > .SRCINFO`
4. Test with `makepkg -f`, then commit.

The same files are what gets pushed to the AUR (`ssh://aur@aur.archlinux.org/smartlinux.git`,
branch `master`) once AUR account registration reopens.
