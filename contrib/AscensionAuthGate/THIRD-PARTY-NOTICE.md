# Third-party attribution

**FirstOni** — original **AscensionAuthGate** design and implementation, including the in-process custom-auth proxy and SRP credential-gate starting point.

**maribela** — Linux/Wine support and LAN deployment (2026-09-10): the address-matched IAT hook that makes the auth redirect land under Wine, the non-MSVC guarded-read implementation that lets the DLL cross-compile with clang, the optional `authgate.cfg` deployment file, the single-address SRP6 opt-in, and the world-endpoint allow-list patch. Contributed as patches and included with the contributor's agreement; see [LAN-AND-LINUX.md](../../docs/LAN-AND-LINUX.md).

The hertigservices Ascension preservation project adapted this supplied source for its local bridge. Subsequent diagnostic removal, SRP/server-proof validation, framing and path hardening, tests, installation scripts and documentation were completed with Codex. These later changes are not attributed to FirstOni or presented as their endorsement.

The supplied AuthGate package contained README/HOW-IT-WORKS documentation but no standalone license file or explicit license grant was found. Its authorship is preserved here. Do not infer that the repository's MIT grant for original hertigservices work relicenses FirstOni's pre-existing contribution. No genuine game client binary or unrelated third-party crypto source is included in this adaptation.
