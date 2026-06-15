# ThoriumReader_sigil

이 플러그인은 Thorium Reader의 Sigil 플러그인 버전입니다.
Thorium Web 1.5.4 소스를 기반으로 Sigil 플러그인에 맞게 개선하였습니다.

# 플러그인 개발 이유
Official Sigil Plugins으로 Readium Reader를 제공하고 있으나 Readium Cloud Reader를 그대로 사용해 몇가지 불편한 점이 있었습니다.
Thorium Web Reader는 Readium 프로젝트를 이어받아 Readium Cloud Reader를 개선한 버전으로 보다 편리한 기능이 추가되었고, 접근성 기능도 반영되어 있습니다.
또한 프로젝트를 종료한 Readium Cloud Reader를 이어받아 지속적으로 기능을 개선하고 있어 지속적으로 갱신하는 EPUB 3.x 기술을 반영하고 있습니다.

Thorium Web Reader에 대한 자세한 설명은 이곳을 참고하세요.
https://github.com/edrlab/thorium-web


# Thorium Reader for Sigil의 특징
Thorium Reader for Sigil은 Thorium Web Reader에 몇가지 기능을 개선하였습니다.

1. 접근성 기능 제한
   - Thorium Reader는 접근성 기능이 강화되어 색상을 강제로 변경하는 것 같습니다.(설정을 찾지 못한 것일 수도 있습니다)
   - Light, Paper, Sepia 및 Auto로 Light, Paper, Sepia 테마를 선택할 경우 EPUB의 CSS 색상을 지원합니다.
   - 그 외 다른 테마를 선택 할 경우 Thorium의 접근성 정책을 따릅니다.   
2. 팝업 주석 지원
   - epub:type="noteref", epub:type="footnote" 일 때 주석을 팝업으로 표시합니다.
3. Progress bar 표시
   - 1024byte를 1페이지로 계산하여 현재 페이지가 전체 페이지 중 어디에 해당하는지 확인 할 수 있습니다. 
   - Progress bar 자체는 Thorium Web Reader의 기본 기능이고, Backend에서 페이지를 계산하는 기능을 추가했습니다.
4. Locale 지원
   - 브라우저 언어(윈도우 기본 언어에서 가져옴)를 기반으로 다국어를 지원합니다.
   - 지원 언어는 플러그인 폴더>Viewer>locales에 있습니다. 
   - 언어를 추가하고 싶다면 https://github.com/edrlab/thorium-web/tree/develop/public/locales 이 곳을 참고하세요.

# 참고
플러그인이 강제 종료될 경우 Reader가 사용한 temp 폴더가 삭제되지 않을 수 있습니다.
temp 폴더는 sigil\plugins\ThoriumReader\temp_[5자리 임의 조합]으로 생성됩니다.
가끔씩 ThoriumReader 플러그인 폴더에 들어가 temp 폴더를 삭제하기 바랍니다.
