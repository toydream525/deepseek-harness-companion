# Third-party notices for the Windows release

DSH 伴航 / Harness Companion's original application code and documentation are
licensed under the MIT license in `LICENSE`. That license does not replace the
licenses of the components below. The project is independent of DeepSeek and
is not affiliated with or endorsed by it.

The Windows ZIP bundles a Python 3.12 interpreter, PySide6/Qt 6.11.2 shared
libraries and plugins, a PyInstaller 6.22.3 bootloader, and the listed Python
download dependencies. It does **not** bundle llama.cpp, Node.js, DeepSeek
Harness, model weights, chat templates obtained from third parties, or GPU
runtimes. Users can choose files they installed themselves or explicitly
prepare the pinned, reviewed llama.cpp/DSH versions from official sources in
separate managed directories. `resources/dsh-install/package-lock.json` is an
integrity-pinned npm dependency recipe, not a bundled DSH installation.

## Qt for Python and Qt

The community PySide6 6.11.2 bindings and the shipped Qt 6.11.2 libraries are
available under LGPL version 3 or GPL version 3, as applicable to each Qt
module. The packaged application uses Qt Widgets, Core, Gui, and Network; Qt
WebEngine, Qt Virtual Keyboard, and Qt PDF are not application requirements.
The full LGPL-3.0 and GPL-3.0 texts are in `licenses/`. Qt and PySide6 DLLs,
extension modules, and plugins remain separate files under `_internal/PySide6`
and `_internal/shiboken6`; recipients can replace compatible libraries. The
project's MIT-licensed source and build instructions are available in the
[public source repository](https://github.com/toydream525/deepseek-harness-companion).

For corresponding upstream source and component-specific notices, see the
[PySide6 6.11.2 source archive](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/),
[Qt 6.11.2 source archive](https://download.qt.io/archive/qt/6.11/6.11.2/single/),
[Qt licensing overview](https://doc.qt.io/qt-6/licensing.html), and
[third-party code used in Qt](https://doc.qt.io/qt-6/licenses-used-in-qt.html).
If you redistribute modified Qt or PySide6 libraries, follow their applicable
license terms and provide the corresponding source and notices.

## Python and packaging

The bundled Python interpreter retains the Python Software Foundation license;
the installed Python 3.12 license text is in `licenses/Python-3.12.txt` and
the official terms are [here](https://docs.python.org/3.12/license.html).
PyInstaller's bootloader is covered by its GPL license with a bundling
exception; the installed copying text is in
`licenses/PyInstaller-COPYING.txt`, with an
[official explanation](https://pyinstaller.org/en/stable/license.html).
Microsoft Visual C++ runtime DLLs included with the Python/Qt distribution
remain subject to their own Microsoft terms.

## Python download dependencies

The bundled Python packages are `huggingface_hub`, `hf_xet`, `httpx`,
`httpcore`, `h11`, `anyio`, `idna`, `certifi`, `filelock`, `fsspec`,
`packaging`, `PyYAML`, `click`, `colorama`, `tqdm`, `typing_extensions`,
and `socksio`. Their exact versions, license metadata, and available license
texts ship under `_internal/_vendor/*.dist-info/`; consult each package's
`METADATA` and `licenses/` or `LICENSE` files. In particular, `certifi` is
MPL-2.0, `hf_xet` and `huggingface_hub` are Apache-2.0, and `tqdm` metadata
identifies MPL-2.0 and MIT terms. The `_vendor` directory contains these
runtime dependencies, not a copy of the application's own source tree.

The license details above describe the packaged version. A source checkout
installs dependencies separately according to `requirements-build.txt` and
`requirements-vendor.txt`; those installs retain their original licenses.

## Windows installer

The optional Windows Setup EXE is built with Inno Setup 6.7.3. Its installer
runtime remains under the [Inno Setup license](https://jrsoftware.org/files/is/license.txt),
included as `licenses/Inno-Setup-LICENSE.txt`. Inno Setup is a build tool, not
part of the portable application's Python or Qt runtime. The installer does
not include model weights, llama.cpp, Node.js, DSH, GPU runtimes or a third-party
chat template.
