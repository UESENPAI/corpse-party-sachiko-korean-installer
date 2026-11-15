"""Recover only strings proven present in a known, user-supplied PSP patch."""

from hashlib import sha256 as _hash

from psp_crilayla import decode_known_island
from psp_jdf import SymbolicJDF
from sachiko_profile import PROFILE


def sha256(data):
    return _hash(data).hexdigest()


def recover_pools(jdf):
    """Recover only profiled, fully known strings; return transient CP932 pools."""
    if len(jdf) != PROFILE['jdf']['size'] or sha256(jdf) != PROFILE['jdf']['sha256']:
        raise ValueError('JDF authentication failed')
    target = SymbolicJDF(jdf)
    if (target.size != PROFILE['jdf']['target_size']
            or len(target.literals) != PROFILE['jdf']['known_bytes']):
        raise ValueError('Unexpected JoJoDiff target/literal layout')
    pools = {}
    for profile in PROFILE['members']:
        data, mask = target.read(profile['iso_offset'], profile['file_size'])
        start, end = profile['compressed_span']
        if (not all(mask[start:end])
                or sha256(data[start:end]) != profile['compressed_sha256']):
            raise ValueError('A reviewed compressed text island is not completely known')
        island, valid = decode_known_island(data, start, end, profile['entry_skip_bits'],
                                            profile['extract_size'] + 8192)
        if (len(island) != profile['island_length']
                or sha256(island) != profile['island_sha256']
                or sha256(valid) != profile['island_mask_sha256']):
            raise ValueError('CRILAYLA island differs from the reviewed reconstruction')
        at, stop = profile['pool_start'], profile['pool_end']
        if (island.rfind(b'\x0d\x02') + 2 != at
                or not all(valid[at:stop])
                or sha256(island[at:stop]) != profile['pool_sha256']):
            raise ValueError('Reconstructed text pool is incomplete or moved')
        values = []
        for _ in range(profile['pool_count']):
            boundary = island.find(b'\0', at, stop)
            if boundary < 0 or not all(valid[at:boundary + 1]):
                raise ValueError('An imported PSP string or terminator is unknown')
            text = island[at:boundary].decode('cp932', 'strict')
            if any(ord(c) < 32 and c not in '\t\n\r' for c in text):
                raise ValueError('Unexpected PSP text control byte')
            values.append(text)
            at = boundary + 1
        if at != stop:
            raise ValueError('PSP pool count does not match the reviewed profile')
        pools[profile['member']] = tuple(values)
    return pools
