#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
import argparse
import tempfile
import shutil
import inspect
import urllib.parse
import threading
import socketserver
import http.server
import socket
import json
import xml.etree.ElementTree as ET

from plugin_utils import QtCore, QtWidgets, QtWebEngineWidgets
from plugin_utils import QWebEnginePage, QWebEngineProfile, QWebEngineScript, QWebEngineSettings
from plugin_utils import PluginApplication

frame = inspect.currentframe()
SCRIPT_DIR = os.path.dirname(os.path.abspath(inspect.getfile(frame))) if frame else os.path.dirname(os.path.abspath(__file__))
_server_thread = None
_httpd = None
_server_port = 0

class EpupToWebPubManifest:
    def __init__(self, epub_dir, base_url):
        self.epub_dir = epub_dir
        self.base_url = base_url
        self.metadata_dict: dict[str, str] = {}
        self.resources_list = []
        self.reading_order_list = []
        self.toc_list = []
        self.manifest = {
            "@context": "https://readium.org/webpub-manifest/context.jsonld",
            "metadata": self.metadata_dict,
            "links": [
                {"rel": "self", "href": f"{base_url}manifest.json", "type": "application/webpub+json"},
                {"rel": "contents", "href": f"{base_url}positions.json", "type": "application/vnd.readium.position-list+json"}
            ],
            "readingOrder": self.reading_order_list,
            "resources": self.resources_list,
            "toc": self.toc_list
        }
        self._parse()

    def _parse(self):
        container_path = os.path.join(self.epub_dir, "META-INF", "container.xml")
        if not os.path.exists(container_path):
            return
        
        tree = ET.parse(container_path)
        root = tree.getroot()
        ns = {'c': 'urn:oasis:names:tc:opendocument:xmlns:container'}
        rootfile = root.find('.//c:rootfile', ns)
        if rootfile is None:
            return
            
        opf_path = rootfile.get('full-path')
        if opf_path is None:
            return
            
        self._parse_opf(os.path.join(self.epub_dir, opf_path), os.path.dirname(opf_path))

    def _parse_opf(self, opf_path, opf_dir):
        if not os.path.exists(opf_path):
            return
            
        tree = ET.parse(opf_path)
        root = tree.getroot()
        ns = {
            'opf': 'http://www.idpf.org/2007/opf',
            'dc': 'http://purl.org/dc/elements/1.1/'
        }

        # Metadata
        metadata = root.find('opf:metadata', ns)
        if metadata is not None:
            title = metadata.find('dc:title', ns)
            t_text = title.text if title is not None else None
            self.metadata_dict['title'] = str(t_text) if t_text else "Unknown Title"
            
            author = metadata.find('dc:creator', ns)
            a_text = author.text if author is not None else None
            if a_text:
                self.metadata_dict['author'] = str(a_text)
                
        # Manifest items
        items = {}
        nav_href = None
        ncx_id = None
        
        manifest_node = root.find('opf:manifest', ns)
        if manifest_node is not None:
            for item in manifest_node.findall('opf:item', ns):
                item_id = item.get('id')
                href = item.get('href')
                media_type = item.get('media-type')
                properties = item.get('properties') or ''
                
                if 'nav' in properties.split():
                    nav_href = href
                
                # Resolve relative path to EPUB root
                full_href = href if not opf_dir else f"{opf_dir}/{href}"
                item_url = f"{self.base_url}epub_content/{full_href}"
                local_path = os.path.join(self.epub_dir, full_href).replace('\\', '/')
                
                item_dict: dict = {
                    "id": item_id,
                    "href": item_url,
                    "type": media_type,
                    "local_path": local_path
                }
                
                if 'cover-image' in properties.split():
                    item_dict['rel'] = ['cover']
                
                items[item_id] = item_dict
                self.resources_list.append(item_dict)

        # Reading Order (Spine)
        spine = root.find('opf:spine', ns)
        if spine is not None:
            ncx_id = spine.get('toc')
            for itemref in spine.findall('opf:itemref', ns):
                idref = itemref.get('idref')
                if idref in items:
                    item_dict = items[idref]
                    self.reading_order_list.append(item_dict)
                    if item_dict in self.resources_list:
                        self.resources_list.remove(item_dict)

        # Parse TOC
        if nav_href:
            nav_path = os.path.join(opf_dir, nav_href) if opf_dir else nav_href
            self._parse_nav_toc(os.path.join(self.epub_dir, nav_path), opf_dir)
        elif ncx_id and ncx_id in items:
            ncx_href = items[ncx_id]["href"].split('/epub_content/')[-1]
            self._parse_ncx_toc(os.path.join(self.epub_dir, urllib.parse.unquote(ncx_href)))

    def _parse_xml_safely(self, file_path):
        import re
        import xml.etree.ElementTree as ET
        if not os.path.exists(file_path): return None
        with open(file_path, 'r', encoding='utf-8') as f:
            xml_string = f.read()
        # Remove DOCTYPE
        xml_string = re.sub(r'<!DOCTYPE[^>]*>', '', xml_string, flags=re.IGNORECASE)
        # Remove namespaces from tags
        xml_string = re.sub(r'\s+xmlns(:\w+)?="[^"]*"', '', xml_string)
        xml_string = re.sub(r'<(\w+):([a-zA-Z0-9_.-]+)', r'<\2', xml_string)
        xml_string = re.sub(r'</(\w+):([a-zA-Z0-9_.-]+)', r'</\2', xml_string)
        # Remove namespaces from attributes
        xml_string = re.sub(r'(\s+)(?!xml:)[a-zA-Z0-9_-]+:([a-zA-Z0-9_-]+)=', r'\1\2=', xml_string)
        try:
            return ET.fromstring(xml_string)
        except Exception as e:
            print(f"Error parsing XML safely: {e}")
            return None

    def _parse_ncx_toc(self, ncx_path):
        root = self._parse_xml_safely(ncx_path)
        if root is None: return
        try:
            navMap = root.find('.//navMap')
            if navMap is not None:
                self.toc_list.extend(self._process_ncx_node(navMap, ncx_path))
        except Exception as e:
            print(f"Error in NCX TOC: {e}")

    def _process_ncx_node(self, parent_node, ncx_path):
        toc = []
        ncx_dir = os.path.dirname(ncx_path)
        for navPoint in parent_node.findall('./navPoint'):
            navLabel = navPoint.find('./navLabel/text')
            content = navPoint.find('./content')
            if navLabel is not None and content is not None:
                import re
                title = "".join(navLabel.itertext())
                title = re.sub(r'\s+', ' ', title).strip()
                src = content.get('src')
                if src:
                    file_src, frag = src.split('#', 1) if '#' in src else (src, '')
                    if file_src:
                        full_src_path = os.path.normpath(os.path.join(ncx_dir, urllib.parse.unquote(file_src))).replace('\\', '/')
                        rel_to_epub = os.path.relpath(full_src_path, self.epub_dir).replace('\\', '/')
                        href = f"{self.base_url}epub_content/{rel_to_epub}"
                    else:
                        rel_to_epub = os.path.relpath(ncx_path, self.epub_dir).replace('\\', '/')
                        href = f"{self.base_url}epub_content/{rel_to_epub}"
                    if frag: href += f"#{frag}"
                else:
                    href = ""
                
                children = self._process_ncx_node(navPoint, ncx_path)
                item: dict = {"title": title, "href": href}
                if children: item["children"] = children
                toc.append(item)
        return toc

    def _parse_nav_toc(self, nav_path, opf_dir):
        root = self._parse_xml_safely(nav_path)
        if root is None: return
        try:
            for nav in root.findall('.//nav'):
                if nav.get('type') == 'toc' or nav.get('epub:type') == 'toc':
                    ol = nav.find('./ol')
                    if ol is not None:
                        self.toc_list.extend(self._process_nav_node(ol, nav_path))
                    break
        except Exception as e:
            print(f"Error in NAV TOC: {e}")

    def _process_nav_node(self, parent_ol, nav_path):
        toc = []
        nav_dir = os.path.dirname(nav_path)
        for li in parent_ol.findall('./li'):
            a = li.find('./a')
            if a is not None:
                import re
                title = "".join(a.itertext())
                if not title.strip():
                    img = a.find('./img')
                    if img is not None: title = img.get('alt', '')
                if not title.strip(): title = a.get('title', '')
                title = re.sub(r'\s+', ' ', title).strip()
                src = a.get('href')
                if src:
                    file_src, frag = src.split('#', 1) if '#' in src else (src, '')
                    if file_src:
                        full_src_path = os.path.normpath(os.path.join(nav_dir, urllib.parse.unquote(file_src))).replace('\\', '/')
                        rel_to_epub = os.path.relpath(full_src_path, self.epub_dir).replace('\\', '/')
                        href = f"{self.base_url}epub_content/{rel_to_epub}"
                    else:
                        rel_to_epub = os.path.relpath(nav_path, self.epub_dir).replace('\\', '/')
                        href = f"{self.base_url}epub_content/{rel_to_epub}"
                    if frag: href += f"#{frag}"
                else:
                    href = ""
                
                child_ol = li.find('./ol')
                children = self._process_nav_node(child_ol, nav_path) if child_ol is not None else []
                item: dict = {"title": title, "href": href}
                if children: item["children"] = children
                toc.append(item)
            else:
                span = li.find('./span')
                if span is not None:
                    import re
                    title = "".join(span.itertext())
                    if not title.strip(): title = span.get('title', '')
                    title = re.sub(r'\s+', ' ', title).strip()
                    child_ol = li.find('./ol')
                    children = self._process_nav_node(child_ol, nav_path) if child_ol is not None else []
                    item_span: dict = {"title": title}
                    if children: item_span["children"] = children
                    toc.append(item_span)
        return toc

    def to_json(self):
        return json.dumps(self.manifest).encode('utf-8')

    def positions_json(self):
        import math
        import re
        import locale
        positions_list = []
        position = 1
        
        # Determine language for position calculation strategy
        lang_code, _ = locale.getdefaultlocale()
        is_korean = lang_code and lang_code.startswith('ko')

        # Calculate total positions first
        total_positions = 0
        chapter_positions = []
        for item in self.reading_order_list:
            local_path = item.get("local_path", "")
            num_positions = 1
            if os.path.exists(local_path):
                if is_korean:
                    text_len = 450 # default fallback
                    try:
                        with open(local_path, 'r', encoding='utf-8') as f:
                            content = f.read()
                        # Remove HTML tags
                        text = re.sub(r'<[^>]+>', '', content)
                        # Collapse whitespaces to accurately count readable characters
                        text = re.sub(r'\s+', ' ', text).strip()
                        text_len = len(text)
                    except Exception:
                        pass
                    # 450 characters per position
                    num_positions = max(1, math.ceil(text_len / 450.0))
                else:
                    # 1024 bytes per position for non-korean
                    file_size = os.path.getsize(local_path)
                    num_positions = max(1, math.ceil(file_size / 1024.0))

            chapter_positions.append(num_positions)
            total_positions += num_positions

        current_progression = 0
        for i, item in enumerate(self.reading_order_list):
            num_pos = chapter_positions[i]
            for j in range(num_pos):
                positions_list.append({
                    "href": item["href"],
                    "type": item["type"],
                    "locations": {
                        "position": position,
                        "progression": j / num_pos,
                        "totalProgression": current_progression / total_positions if total_positions > 0 else 0.0
                    }
                })
                position += 1
                current_progression += 1
                
        return json.dumps({
            "total": len(positions_list),
            "positions": positions_list
        }).encode('utf-8')


class CustomHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    epub_dir = ""
    manifest_bytes = b""
    positions_bytes = b""
    prefs = {}
    bk_ref = None

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        
        # Intercept manifest.json request
        if parsed.path == "/manifest.json":
            self.send_response(200)
            self.send_header('Content-Type', 'application/webpub+json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(self.manifest_bytes)
            return

        if parsed.path == "/settings.json":
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(self.prefs).encode('utf-8'))
            return

        if parsed.path == "/positions.json":
            self.send_response(200)
            self.send_header('Content-Type', 'application/vnd.readium.position-list+json; charset=utf-8')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(self.positions_bytes)
            return

        # Serve epub_content
        path_str = str(parsed.path)
        if path_str.startswith('/epub_content/'):
            # Strip /epub_content/ and serve from temporary epub_dir
            relative_path = path_str.replace('/epub_content/', '', 1).lstrip('/')
            target_path = os.path.normpath(os.path.join(self.epub_dir, urllib.parse.unquote(relative_path)))
            
            if os.path.exists(target_path) and not os.path.isdir(target_path):
                self.send_response(200)
                # Quick mimetype guess
                if target_path.endswith('.css'): self.send_header('Content-Type', 'text/css')
                elif target_path.endswith('.js'): self.send_header('Content-Type', 'application/javascript')
                elif target_path.endswith('.html') or target_path.endswith('.xhtml'): self.send_header('Content-Type', 'application/xhtml+xml')
                elif target_path.endswith('.png'): self.send_header('Content-Type', 'image/png')
                elif target_path.endswith('.jpg') or target_path.endswith('.jpeg'): self.send_header('Content-Type', 'image/jpeg')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                
                with open(target_path, 'rb') as f:
                    self.wfile.write(f.read())
                return
            else:
                self.send_error(404, "File not found")
                return

        # Serve static viewer files (dist directory)
        viewer_dir = os.path.join(SCRIPT_DIR, 'viewer')
        if parsed.path == '/':
            target_path = os.path.join(viewer_dir, 'index.html')
        else:
            target_path = os.path.join(viewer_dir, urllib.parse.unquote(parsed.path.lstrip('/')))

        if os.path.exists(target_path) and not os.path.isdir(target_path):
            self.send_response(200)
            if target_path.endswith('.css'): self.send_header('Content-Type', 'text/css')
            elif target_path.endswith('.js') or target_path.endswith('.mjs'): self.send_header('Content-Type', 'application/javascript')
            elif target_path.endswith('.html'): self.send_header('Content-Type', 'text/html')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with open(target_path, 'rb') as f:
                self.wfile.write(f.read())
            return

        print(f"HTTP ERROR: 404 Not Found -> {parsed.path}", file=sys.stderr)
        self.send_error(404, "File not found")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/settings.json":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                new_prefs = json.loads(post_data.decode('utf-8'))
                if hasattr(self, 'bk_ref') and self.bk_ref:
                    self.bk_ref.savePrefs(new_prefs)
                    self.prefs = new_prefs
            except Exception as e:
                print(f"Error saving prefs: {e}")
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            return


class WebPage(QWebEnginePage):
    def __init__(self, profile, parent=None):
        QWebEnginePage.__init__(self, profile, parent)

    def javaScriptConsoleMessage(self, level, msg, linenumber, source_id):
        prefix = {
            QWebEnginePage.InfoMessageLevel: 'JS INFO',
            QWebEnginePage.WarningMessageLevel: 'JS WARNING'
        }.get(level, 'JS ERROR')
        print(f"{prefix}: {source_id}:{linenumber}: {msg}")

    def acceptNavigationRequest(self, url, req_type, is_main_frame):
        return True

class WebView(QtWebEngineWidgets.QWebEngineView):
    def __init__(self, parent=None):
        QtWebEngineWidgets.QWebEngineView.__init__(self, parent)
        app = PluginApplication.instance()
        w = app.primaryScreen().availableGeometry().width()
        self._size_hint = QtCore.QSize(int(w/2), int(w/1.5))
        
        self._profile = QWebEngineProfile(self)
        self._profile.setHttpCacheType(QWebEngineProfile.MemoryHttpCache)
        
        import locale
        lang_code, _ = locale.getdefaultlocale()
        if lang_code:
            primary_lang = lang_code.split('_')[0]
            if primary_lang == 'ko':
                primary_lang = 'kr'
            self._profile.setHttpAcceptLanguage(f"{primary_lang},en-US;q=0.9,en;q=0.8")
        
        # Inject script to intercept EPUB footnotes in iframes
        script = QWebEngineScript()
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setRunsOnSubFrames(True)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setSourceCode("""
            document.addEventListener('click', function(e) {
                var target = e.target.closest('a');
                if (target) {
                    var isNoteRef = target.getAttribute('epub:type') === 'noteref' || 
                                    target.getAttribute('type') === 'noteref' ||
                                    (target.getAttribute('role') && target.getAttribute('role').includes('doc-noteref'));
                    if (isNoteRef) {
                        e.preventDefault();
                        e.stopPropagation();
                        var href = target.getAttribute('href');
                        if (href) {
                            try {
                                var absoluteUrl = new URL(href, document.baseURI).href;
                                window.top.postMessage({ type: 'THORIUM_FOOTNOTE', href: absoluteUrl }, '*');
                            } catch (err) {}
                        }
                    }
                }
            }, true);

            document.addEventListener('keydown', function(e) {
                if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
                    window.top.postMessage({ type: 'THORIUM_KEY', key: e.key }, '*');
                }
            }, true);
        """)
        self._profile.scripts().insert(script)
        
        self._page = WebPage(self._profile, self)
        self.setPage(self._page)
        
        s = self.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        
    def sizeHint(self): return self._size_hint

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, bk, prefs, *args, **kwargs):
        QtWidgets.QMainWindow.__init__(self, *args, **kwargs)
        self.bk = bk
        self.prefs = prefs
        self.browser = WebView()
        
        base_url = f"http://127.0.0.1:{_server_port}/"
        self.browser.setUrl(QtCore.QUrl(base_url))
        self.setCentralWidget(self.browser)
        
        geom = self.prefs.get('geometry')
        if geom and len(geom) == 4:
            self.setGeometry(geom[0], geom[1], geom[2], geom[3])
        else:
            self.resize(self.browser.sizeHint())
            
        self.show()

    def closeEvent(self, event):
        geom = self.geometry()
        self.prefs['geometry'] = [geom.x(), geom.y(), geom.width(), geom.height()]
        self.bk.savePrefs(self.prefs)
        super().closeEvent(event)

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def start_http_server(port, epub_dir, bk=None, prefs=None):
    global _httpd, _server_thread, _server_port
    _server_port = port
    
    base_url = f"http://127.0.0.1:{port}/"
    manifest_generator = EpupToWebPubManifest(epub_dir, base_url)
    manifest_bytes = manifest_generator.to_json()
    positions_bytes = manifest_generator.positions_json()

    Handler = CustomHTTPRequestHandler
    Handler.epub_dir = epub_dir
    Handler.manifest_bytes = manifest_bytes
    Handler.positions_bytes = positions_bytes
    if prefs is not None:
        Handler.prefs = prefs
    if bk is not None:
        Handler.bk_ref = bk

    _httpd = socketserver.TCPServer(("127.0.0.1", port), Handler, bind_and_activate=False)
    _httpd.allow_reuse_address = True
    _httpd.server_bind()
    _httpd.server_activate()

    _server_thread = threading.Thread(target=_httpd.serve_forever)
    _server_thread.daemon = True
    _server_thread.start()

def stop_http_server():
    global _httpd, _server_thread
    if _httpd:
        _httpd.shutdown()
        _httpd.server_close()
        _httpd = None

def run(bk):
    import random
    import string
    
    # Try to disable GPU to prevent blank screen issues in some internal Qt WebEngine versions
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = "--disable-gpu --disable-gpu-compositing"
    print(f"DEBUG: QTWEBENGINE_CHROMIUM_FLAGS set to: {os.environ.get('QTWEBENGINE_CHROMIUM_FLAGS')}")

    print("Thorium Reader Starting...")
    random_suffix = ''.join(random.choices(string.ascii_letters + string.digits, k=5))
    bookdir = os.path.join(SCRIPT_DIR, f'temp_{random_suffix}')
    os.makedirs(bookdir, exist_ok=True)
    print(f"Extracting EPUB to {bookdir}")
    bk.copy_book_contents_to(bookdir)

    port = find_free_port()
    print(f"Starting server on port {port}")
    prefs = bk.getPrefs()
    start_http_server(port, bookdir, bk, prefs)

    icon = os.path.join(bk._w.plugin_dir, bk._w.plugin_name, 'plugin.svg') if hasattr(bk, '_w') else ''
    app = PluginApplication(sys.argv, bk, app_icon=icon)
    app.setApplicationName("Thorium Reader Sigil Plugin")

    window = MainWindow(bk, prefs)
    
    print("Executing Qt App Main Loop")
    app.exec_()
    
    print("Cleaning up...")
    try:
        if os.path.exists(bookdir):
            shutil.rmtree(bookdir)
    except Exception as e:
        print(f"Cleanup error: {e}", file=sys.stderr)
        
    stop_http_server()
    print("Thorium Reader Closed.")
    return 0
