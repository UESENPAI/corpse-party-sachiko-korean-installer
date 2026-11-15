#!/usr/bin/env python3
"""Install a local Sachiko PSP-donor conversion on a supported Steam build."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import installer
from project_config import GAME, ROOT


def selected_game(path):
    if path is not None:
        return path
    games = installer.discover_games()
    if not games:
        raise installer.PatchError('게임을 찾지 못했습니다. Steam 설치를 확인하거나 --game-dir로 지정하세요.')
    if len(games) != 1:
        raise installer.PatchError('설치가 여러 개입니다. --game-dir로 선택하세요:\n'
                                   + '\n'.join(game['path'] for game in games))
    return Path(games[0]['path'])


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure):
            reconfigure(encoding='utf-8', errors='backslashreplace')
    parser = argparse.ArgumentParser(description=GAME['title'] + ' — PSP 번역 이식 미리보기')
    parser.add_argument('action', choices=('check', 'install', 'uninstall', 'status', 'detect', 'inspect'),
                        nargs='?', default='check')
    parser.add_argument('--game-dir', '--path', type=Path, help='생략하면 Steam 설치 경로 자동 검색')
    parser.add_argument('--psp-patch', type=Path, help='원 배포처에서 받은 PSP 한글패치 7z 파일')
    parser.add_argument('--dry-run', action='store_true', help='설치 또는 복원을 실제 적용하지 않고 확인')
    parser.add_argument('--json', action='store_true', help='JSON 형식으로 결과 출력')
    args = parser.parse_args(argv)
    try:
        if args.action in ('detect', 'status', 'uninstall'):
            forwarded = [args.action]
            if args.game_dir is not None:
                forwarded += ['--game-dir', str(args.game_dir)]
            if args.dry_run:
                forwarded.append('--dry-run')
            if args.json:
                forwarded.append('--json')
            return installer.main(forwarded)

        from psp_import import selected_patch, verified_archive
        if args.action == 'inspect':
            donor = selected_patch(args.psp_patch)
            archive = verified_archive(donor)
            print(json.dumps({'filename': donor.name, 'size': len(archive), 'format': '7z',
                              'sha256': hashlib.sha256(archive).hexdigest(), 'known_input': True},
                             ensure_ascii=False, indent=2))
            return 0

        game = selected_game(args.game_dir)
        donor = selected_patch(args.psp_patch)
        check = args.action == 'check'
        dry_run = check or args.dry_run
        if not args.json:
            print('PSP 번역에서 Steam 리소스를 재구성하고 있습니다.', file=sys.stderr)
        result = installer.install(game, ROOT / 'payload', dry_run=dry_run, psp_patch=donor)
        if check:
            result.update(action='check', compatible=True)
        result['release_ready'] = bool(GAME['release_ready'])
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif dry_run:
            print(f"검사 완료: {result['game_dir']} ({result['files']}개 리소스, 게임 파일 변경 없음)")
        else:
            print(f"설치 완료: {result['game_dir']} ({result['changed_files']}개 파일)")
        return 0
    except (installer.PatchError, ValueError, OSError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False) if args.json else str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
