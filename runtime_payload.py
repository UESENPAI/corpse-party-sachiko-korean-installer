"""Materialize verified PSP donor references into temporary Steam resources."""
from pathlib import Path


def materialize(game, recipe, archive, manifest, output):
    from installer import PatchError, safe_relative, SHA256
    from recipe_apply import prepare

    try:
        generated = prepare(game, recipe, archive, Path(output) / 'files')
        entries = {}
        for entry in generated:
            name = safe_relative(entry['path'])
            if name.casefold() in {p.casefold() for p in entries}:
                raise ValueError('Duplicate generated game resource')
            if not SHA256.fullmatch(entry['sha256']):
                raise ValueError('Invalid generated resource checksum')
            if not entry.get('source_sha256') or any(not SHA256.fullmatch(s) for s in entry['source_sha256']):
                raise ValueError('Generated game resource has no original fingerprint')
            entries[name] = entry
        if not entries:
            raise ValueError('Recipe generated no resources')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise PatchError(f'Cannot rebuild localized resources: {exc}') from exc
    return Path(output), {**manifest, 'files': list(entries.values())}, entries
