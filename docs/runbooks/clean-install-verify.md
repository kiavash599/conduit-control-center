# Clean-install trust bootstrap (verify BEFORE extract)

The installer (`install.sh`) and the embedded armv7 wheelhouse must be consumed ONLY from an
artifact tree that has already passed signature + platform verification. Do NOT run an installer that
was extracted from an unverified artifact (that is circular). Use `ccc-verify-release` first, from a
**trusted git checkout**, with a publisher anchor obtained **out-of-band**.

## Steps

1. Obtain the publisher anchor out-of-band. Confirm the publisher key fingerprint from the project's
   documented, trusted channel (release page / README over TLS). Save it as `allowed_signers`
   (principal `conduit-control-center-publisher`). **Never take the anchor from the artifact.**
2. Download the release assets for your platform: `ccc-X.Y.Z-<arch>.tar.gz`,
   `ccc-X.Y.Z.manifest.json`, `ccc-X.Y.Z.manifest.json.sig` (`<arch>` = `uname -m`).
3. Verify BEFORE extracting, from a trusted checkout of this repo:
   ```
   python3 deployment/bin/ccc-verify-release \
       --manifest ccc-X.Y.Z.manifest.json \
       --signature ccc-X.Y.Z.manifest.json.sig \
       --artifact  ccc-X.Y.Z-$(uname -m).tar.gz \
       --trust-store allowed_signers
   ```
   Exit 0 prints the canonical filename to extract. Any non-zero exit = STOP (do not extract/install).
   Stock-tools fallback (no wrapper): `ssh-keygen -Y verify -f allowed_signers -I
   conduit-control-center-publisher -n ccc-update-manifest -s ccc-X.Y.Z.manifest.json.sig <
   ccc-X.Y.Z.manifest.json` then confirm `sha256sum ccc-X.Y.Z-$(uname -m).tar.gz` equals the value in
   the manifest entry for your platform.
4. ONLY after a clean verify, extract the artifact and run `sudo bash install.sh` from the extracted
   tree. On armv7l the embedded `wheelhouse-armhf/` is used automatically; never side-load an
   unsigned wheelhouse.
