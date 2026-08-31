# Releasing

kbox's release process is tied to real karaoke events rather than a fixed
schedule, since the only reliable way to validate a change is to run it on
actual hardware in front of actual singers.

## Process

1. **Cut a pre-release before an event.** Tag `vX.Y.Z-rc.N` (e.g.
   `v0.3.0-rc.1`) and push the tag. This triggers
   [docker-publish.yml](../../.github/workflows/docker-publish.yml), which
   builds and pushes a multi-arch image to `mdzz/kbox:X.Y.Z-rc.N` without
   touching the `latest` tag.
2. **Test on real hardware at the event.** Deploy the `-rc` image to the
   Raspberry Pi (or whatever's running that event). If something needs
   fixing, land the fix and cut `-rc.2`, `-rc.3`, etc. — as many as it takes.
3. **Tag the release after a clean event.** Once an `-rc` build has actually
   run a full event without issues, tag it as the release with the suffix
   dropped: `vX.Y.Z`. This pushes `mdzz/kbox:X.Y.Z` and moves the `latest`
   tag to it.

`latest` therefore always points at a build that has been verified at a real
event — never an untested pre-release.

## Tagging

```bash
git tag -a v0.3.0-rc.1 -m "Pre-release for <event name>"
git push origin v0.3.0-rc.1

# ...after a clean event...

git tag -a v0.3.0 -m "Verified at <event name>"
git push origin v0.3.0
```
