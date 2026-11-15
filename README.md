# 사치코의 연애 유희 한국어 이식 설치기

**Corpse Party: Sweet Sachiko's Hysteric Birthday Bash** · Steam App ID `778400` · `0.1.0-psp-donor`

사용자가 직접 준비한 한방콤보의 PSP 한국어 패치에서 대사를 읽어 Steam PC판에 적용합니다. 이 저장소는 설치기 소스입니다.

## 설치기 다운로드

- [Windows 포터블 ZIP](https://github.com/UESENPAI/corpse-party-sachiko-korean-installer/releases/download/v0.1.0-psp-donor/corpse-party-sachiko-psp-donor-0.1.0-windows-portable.zip)
- [Linux .run 설치기](https://github.com/UESENPAI/corpse-party-sachiko-korean-installer/releases/download/v0.1.0-psp-donor/corpse-party-sachiko-psp-donor-0.1.0-linux.run)
- [SHA-256 체크섬](https://github.com/UESENPAI/corpse-party-sachiko-korean-installer/releases/download/v0.1.0-psp-donor/SHA256SUMS.txt)

## 게임과 원본 한패

- [공식 Steam 상점 페이지](https://store.steampowered.com/app/778400/)
- [원본 PSP 한패 다운로드 안내 · 네이버 카페 배포글](https://cafe.naver.com/hansicgu/12823)
- **원본 PSP 한패 제작**은 **한방콤보** 및 원 배포글에 기재된 참여자분들이 맡았습니다.

## 적용 범위

확인된 장면 스크립트 6개에서 영문·일문 아카이브마다 문자열 5,745필드에 PSP 패치의 한국어를 적용합니다. 같은 문구가 여러 필드에서 쓰일 수 있습니다. 해당 범위 밖의 장면, 메뉴, 이미지에는 Steam 원본을 사용합니다.

설치기는 로컬 Steam 원본에서 `scene.bcx`, `scene_jpn.bcx`, `font.bcx`를 재구성합니다. 한글 글꼴은 Noto Sans KR을 사용해 로컬에서 생성합니다.

## 준비물

- 지원되는 Steam PC판 설치본.
- 원 배포처에서 직접 받은 `Corpse_party_2U_JPN.iso-jptch.7z`.
- Windows 포터블 ZIP에는 실행용 Python이 포함되어 있습니다. Linux 설치기와 소스에서 직접 실행할 때는 Python 3.10 이상이 필요하며, Linux에서는 시스템 FreeType도 필요합니다.

지원하는 7z의 SHA-256은 `dc48a2cca114b51ce22617d87345f7912f577935506240fafa8890c34e7be93c`입니다. 설치기는 7z에서 번역에 필요한 JDF 데이터를 읽습니다. 별도 7-Zip이나 PSP ISO는 필요하지 않습니다. 동봉 실행 파일은 실행하지 않습니다.

## 검사와 설치

소스 폴더에서 실행합니다. Linux에서는 `python3`를 사용할 수 있습니다. 설치기가 Steam 설치 위치와 해당 패치 파일을 자동으로 찾으면 경로를 생략할 수 있습니다.

```text
python patch.py check
python patch.py install
python patch.py status
python patch.py uninstall
```

`check`는 설치 가능 여부와 리소스 재구성을 검사합니다. `uninstall`은 관리하는 파일을 원본 백업으로 복원합니다. `status`와 `uninstall`에는 PSP 패치 파일이 필요하지 않습니다.

이전 설치기가 관리한 원본 백업이 유효하면 기존 설치를 갱신하면서 `system.bcx`와 `system_jpn.bcx`를 Steam 원본으로 복원합니다. 설치기가 관리하는 파일은 게임 리소스와 복원용 기록입니다.

여러 설치본이 있거나 패치를 찾지 못하면 경로를 지정합니다. 아래 예시의 경로를 실제 위치로 바꾸세요.

```text
python patch.py check --game-dir "Steam 게임 폴더" --psp-patch "Corpse_party_2U_JPN.iso-jptch.7z"
python patch.py install --game-dir "Steam 게임 폴더" --psp-patch "Corpse_party_2U_JPN.iso-jptch.7z"
python patch.py uninstall --game-dir "Steam 게임 폴더"
```

## 검증과 크레딧

지원 범위는 확인된 Steam 원본과 지정 PSP 입력으로 한정합니다. 실제 게임 화면과 전체 플레이 검증은 별도입니다. [크레딧 및 라이선스](CREDITS.md)
