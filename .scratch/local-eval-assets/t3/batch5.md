

=== TASK PyCQA__flake8-1440 | repo PyCQA/flake8 | python
--- Issue
add color support for formatting
~essentially replace `flake8-colors`

(needs a bit of design before implementation)
--- REAL FIX (ground truth)
diff --git a/docs/source/user/options.rst b/docs/source/user/options.rst
index faeb17e..9cdb0ee 100644
--- a/docs/source/user/options.rst
+++ b/docs/source/user/options.rst
@@ -40,6 +40,8 @@ Index of Options
 
 - :option:`flake8 --quiet`
 
+- :option:`flake8 --color`
+
 - :option:`flake8 --count`
 
 - :option:`flake8 --diff`
@@ -181,6 +183,35 @@ Options and their Descriptions
 
         quiet = 1
 
+.. option:: --color
+
+    :ref:`Go back to index <top>`
+
+    Whether to use color in output. Defaults to ``auto``.
+
+    Possible options are ``auto``, ``always``, and ``never``.
+
+    This **can** be specified in config files.
+
+    When color is enabled, the following substitutions are enabled:
+
+    - ``%(bold)s``
+    - ``%(black)s``
+    - ``%(red)s``
+    - ``%(green)s``
+    - ``%(yellow)s``
+    - ``%(blue)s``
+    - ``%(magenta)s``
+    - ``%(cyan)s``
+    - ``%(white)s``
+    - ``%(reset)s``
+
+    Example config file usage:
+
+    .. code-block:: ini
+
+        color = never
+
 
 .. option:: --count
 
diff --git a/src/flake8/formatting/_windows_color.py b/src/flake8/formatting/_windows_color.py
new file mode 100644
index 0000000..1d2c73f
--- /dev/null
+++ b/src/flake8/formatting/_windows_color.py
@@ -0,0 +1,59 @@
+"""ctypes hackery to enable color processing on windows.
+
+See: https://github.com/pre-commit/pre-commit/blob/cb40e96/pre_commit/color.py
+"""
+import sys
+
+if sys.platform == "win32":  # pragma: no cover (windows)
+
+    def _enable() -> None:
+        from ctypes import POINTER
+        from ctypes import windll
+        from ctypes import WinError
+        from ctypes import WINFUNCTYPE
+        from ctypes.wintypes import BOOL
+        from ctypes.wintypes import DWORD
+        from ctypes.wintypes import HANDLE
+
+        STD_ERROR_HANDLE = -12
+        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 4
+
+        def bool_errcheck(result, func, args):
+            if not result:
+                raise WinError()
+            return args
+
+        GetStdHandle = WINFUNCTYPE(HANDLE, DWORD)(
+            ("GetStdHandle", windll.kernel32),
+            ((1, "nStdHandle"),),
+        )
+
+        GetConsoleMode = WINFUNCTYPE(BOOL, HANDLE, POINTER(DWORD))(
+            ("GetConsoleMode", windll.kernel32),
+            ((1, "hConsoleHandle"), (2, "lpMode")),
+        )
+        GetConsoleMode.errcheck = bool_errcheck
+
+        SetConsoleMode = WINFUNCTYPE(BOOL, HANDLE, DWORD)(
+            ("SetConsoleMode", windll.kernel32),
+            ((1, "hConsoleHandle"), (1, "dwMode")),
+        )
+        SetConsoleMode.errcheck = bool_errcheck
+
+        # As of Windows 10, the Windows console supports (some) ANSI escape
+        # sequences, but it needs to be enabled using `SetConsoleMode` first.
+        #
+        # More info on the escape sequences supported:
+        # https://msdn.microsoft.com/en-us/library/windows/desktop/mt638032(v=vs.85).aspx
+        stderr = GetStdHandle(STD_ERROR_HANDLE)
+        flags = GetConsoleMode(stderr
--- Proposal A
## Root cause
`flake8` prints its results using the built‑in *default* formatter, which always emits plain text.  There is no option to enable ANSI colour codes, so users who previously relied on the external `flake8‑colors` plugin cannot get coloured output out‑of‑the‑box.  The formatter therefore needs a small redesign to optionally wrap parts of the output (filename, line/column numbers, error code, message) in colour when the user requests it (or when output is a TTY).

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/flake8/options.py
+++ b/flake8/options.py
@@
 def add_options(parser):
@@
     parser.add_argument(
         "--statistics",
         action="store_true",
         help="Show statistics about the errors/warnings reported.",
     )
+
+    # ------------------------------------------------------------------
+    # Colour output support (new)
+    # ------------------------------------------------------------------
+    colour_group = parser.add_mutually_exclusive_group()
+    colour_group.add_argument(
+        "--color",
+        dest="color",
+        action="store_true",
+        default=False,
+        help="Enable coloured output (requires a TTY).",
+    )
+    colour_group.add_argument(
+        "--no-color",
+        dest="color",
+        action="store_false",
+        help="Disable coloured output (default).",
+    )
*** End of File
--- a/flake8/main.py
+++ b/flake8/main.py
@@
 from flake8 import defaults, utils
 from flake8.formatting import base as formatting_base
 from flake8.formatting import default as formatting_default
 from flake8.formatting import json as formatting_json
+# colour support
+try:
+    import colorama
+except Exception:  # pragma: no cover – colour is optional
+    colorama = None
@@
 def main(argv=None):
@@
     options = parser.parse_args(argv)
@@
-    # Initialise the formatter
-    formatter = formatting_default.DefaultFormatter(options)
+    # Initialise colour handling (if requested)
+    if options.color and colorama is not None:
+        # Initialise colourama on Windows; on POSIX this is a no‑op.
+        colorama.init()
+
+    # Initialise the formatter (colour handling is delegated to the formatter)
+    formatter = formatting_default.DefaultFormatter(options)
*** End of File
--- a/flake8/formatting/default.py
+++ b/flake8/formatting/default.py
@@
 from __future__ import annotations
@@
 from flake8.formatting import base
@@
 class DefaultFormatter(base.BaseFormatter):
@@
     def format(self, error: Error) -> str:
-        return f"{error.filename}:{error.line_number}:{error.column_number}: {error.code} {error.text}"
+        # If colour is enabled and we are writing to a TTY, wrap parts of the
+        # output in ANSI colour codes.  The colour choice mirrors the behaviour
+        # of the historic ``flake8‑colors`` plugin.
+        if getattr(self.options, "color", False) and self._is_tty():
+            # Lazy import to avoid pulling colourama unless needed.
+            try:
+                from colorama import Fore, Style
+            except Exception:  # pragma: no cover
+                # Fallback to plain output if colourama is unavailable.
+                return (
+                    f"{error.filename}:{error.line_number}:{error.column_number}: "
+                    f"{error.code} {error.text}"
+                )
+
+            filename = f"{Fore.CYAN}{error.filename}{Style.RESET_ALL}"
+            line_col = (
+                f"{For
--- Proposal B
## Root cause
`flake8` formats its output using the built‑in *default* formatter, which always emits plain text.  There is no built‑in way to enable ANSI colour codes, so users have to rely on the external **flake8‑colors** plugin.  The lack of a native colour option means the CLI cannot honour a `--color` flag, cannot automatically enable colours when the output is a TTY, and cannot be configured via the configuration file.

## Fix
Add native colour support:

1. **Introduce a new CLI option `--color`** (choices: `auto`, `always`, `never`; default `auto`).  
2. **Initialise `colorama`** so that colour works on Windows.  
3. **Extend the default formatter** to colour the *code* part of each message (`E` → red, `W` → yellow, `F` → magenta, others → cyan) when colour is enabled.  
4. Pass the parsed colour setting to the formatter via the existing `OptionManager`/formatter constructor path.  

### Files changed
```diff
--- a/src/flake8/options/manager.py
+++ b/src/flake8/options/manager.py
@@
 class OptionManager:
@@
     def __init__(self, version, prog, description):
         ...
         self.add_option(
             '--format',
@@
         )
+        # -----------------------------------------------------------------
+        # Colour output handling (new)
+        # -----------------------------------------------------------------
+        self.add_option(
+            '--color',
+            default='auto',
+            parse_from_config=True,
+            choices=('auto', 'always', 'never'),
+            help=(
+                'Enable coloured output. "auto" (default) enables colours only '
+                'when the output stream is a TTY. "always" forces colour, '
+                'and "never" disables it.'
+            ),
+        )
@@
     def parse_args(self, args):
         ...
         # after parsing, the options namespace is returned
         return self.options
```

```diff
--- a/src/flake8/main.py
+++ b/src/flake8/main.py
@@
 import sys
+import os
+from colorama import init as colorama_init
@@
 def main(argv=None):
@@
-    # Initialise the option manager and parse arguments
+    # Initialise colour handling (must happen before any output)
+    colorama_init()
+
+    # Initialise the option manager and parse arguments
@@
-    formatter = formatter_class(options)
+    formatter = formatter_class(options)
@@
     # Run the checks
@@
```

```diff
--- a/src/flake8/formatting/default.py
+++ b/src/flake8/formatting/default.py
@@
 from __future__ import annotations
@@
 import sys
+from typing import Iterable
+
+from colorama import Fore, Style
@@
 class DefaultFormatter(BaseFormatter):
@@
-    def __init__(self, options):
-        super().__init__(options)
+    def __init__(self, options):
+        super().__init__(options)
+        # Determine whether colour should be used
+        self._color_mode = options.color  # 'auto' | 'always' | 'never'
+        self._use_color = (
+            self._color_mode == 'always'
+            or (self._color_mode == 'auto' and sys.stdout.isatty())
+        )
+
+    # -----------------------------------------------------------------
+    # Colour helpers
+    # -----------------------------------------------------------------
+    @staticmethod
+    def _code_to_color(code: str) -> str:
+        """Map a flake8 error code prefix to an ANSI colour."""
+        if code.startswith('E'):
+            return Fore.RED
+        if code.startswith('W'):
+            return Fore.YELLOW
+        if code.

=== TASK kozea__weasyprint-2494 | repo Kozea/WeasyPrint | python
--- Issue
Bug with columns
I've noticed that sometimes splitting into columns are incorrect. Here is a way that may reproduce it. You may need it few times to notice a bug.

```python
import random
import string
from weasyprint import HTML

h = f"""
<html><body>"""

text = ''

# prefill
for _ in range(60):
    text += ' ' + random.choice(string.ascii_letters) * random.randint(10,20)

# growing text with different words. Maybe it will create the `bad` one.
for p in range(20):
    word_length = random.randint(10, 15)
    text += ' ' + random.choice(string.ascii_letters) * word_length
    h += "<div style='columns: 2; page-break-before: always; '><p>"
    s = str(p) + ' --- ' + text
    h += s
    h += "</p></div>"
h += "</body></html>"
print(h)
doc = HTML(string=h).render()
doc.write_pdf('two.pdf')

```
Here is an example output with problem.
![image](https://user-images.githubusercontent.com/4893428/229906578-7091d42d-9f7c-451a-82fc-20b13bc9607b.png)
In this case the problem is on pages 9 and 10. There is only one narrow column.

It happen only when text fully fit in one column and reaches page bottom. If there is less or more text, then everything ok.

Also it never happens on first page. Looks like `page-break-before` somehow involved.


--- REAL FIX (ground truth)
diff --git a/weasyprint/layout/column.py b/weasyprint/layout/column.py
index 691ba8cb..375141f8 100644
--- a/weasyprint/layout/column.py
+++ b/weasyprint/layout/column.py
@@ -190,6 +190,7 @@ def columns_layout(context, box, bottom_space, skip_stack, containing_block,
                         in_flow_children[-1].margin_height() +
                         in_flow_children[-1].position_y - current_position_y)
                     empty_space = height - consumed_height
+                    consumed_height -= in_flow_children[-1].margin_bottom
 
                     # Get the minimum size needed to render the next box
                     if column_skip_stack:

--- Proposal A
**## Root cause**

When a block with `columns: N` is preceded by `page-break-before: always`, the layout engine starts the block on a new page but **does not reset the internal column‑segment state** (`column_index` and the vertical cursor inside the column).  
If the previous page ended exactly at the bottom of a column, the engine thinks it is already in the *second* column of the new page. Consequently the first column is skipped and only the second (narrow) column is rendered, which is what the bug shows on pages 9‑10.

**## Fix**

Reset the column‑segment state whenever a forced page break (`page-break-before: always`) starts a new multi‑column block.

```diff
--- a/weasyprint/formatting_structure/boxes.py
+++ b/weasyprint/formatting_structure/boxes.py
@@
 class BlockBox(Box):
@@
     def layout(self, context, page, containing_block):
         """Layout the block and its children."""
@@
         # -----------------------------------------------------------------
         # Multi‑column handling
         # -----------------------------------------------------------------
         column_count = self.style.get('column_count', 1)
         if column_count > 1:
-            column_gap = self.style.get('column_gap', 0)
-            column_width = (
-                (containing_block.width - (column_count - 1) * column_gap)
-                / column_count
-            )
-            column_height = containing_block.height
-
-            # The column index (0‑based) and the y‑position inside the current
-            # column are stored on the box so that they survive page breaks.
-            column_index = getattr(self, '_column_index', 0)
-            column_y = getattr(self, '_column_y', 0)
+            column_gap = self.style.get('column_gap', 0)
+            column_width = (
+                (containing_block.width - (column_count - 1) * column_gap)
+                / column_count
+            )
+            column_height = containing_block.height
+
+            # -----------------------------------------------------------------
+            # **Bug fix** – reset column state after a forced page break.
+            #
+            # When a block starts with `page-break-before: always` we must start
+            # the column layout as if it were the very first column on a fresh
+            # page.  Previously the engine kept the values that were left over
+            # from the previous block, which could make the first column be
+            # skipped (the layout started directly with column_index == 1).
+            # -----------------------------------------------------------------
+            if self.style.get('page_break_before') == 'always':
+                # Force the first column on the new page.
+                column_index = 0
+                column_y = 0
+                # Store the reset values so that the rest of the layout code
+                # (which expects the attributes to be present) works unchanged.
+                self._column_index = column_index
+                self._column_y = column_y
+            else:
+                # The column index (0‑based) and the y‑position inside the current
+                # column are stored on the box so that they survive page breaks.
+                column_index = getattr(self, '_column_index', 0)
+                column_y = getattr(self, '_column_y', 0)
@@
             for child in self.children:
                 # Layout each child inside the current column.
        
--- Proposal B
## Root cause
The column layout algorithm tries to *balance* columns by computing the number of columns that fit into the available height.  
When a `<div>` with `columns: 2` and `page‑break‑before: always` starts on a new page, the algorithm sees that the whole content fits into a single column whose height exactly matches the page height.  
Because the content height equals the page height, the balancing code decides that only **one** column is needed and therefore reduces the column count to 1.  
The CSS `columns: 2` (i.e. an explicit `column-count`) is then ignored, which produces the narrow single‑column layout observed on pages 9‑10.

The bug is triggered only when the content exactly fills one column and the page break is forced, which explains why it never appears on the first page and why a slight change in the amount of text makes the problem disappear.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/weasyprint/layout/columns.py
+++ b/weasyprint/layout/columns.py
@@
 def split_into_columns(box, available_width, available_height, column_gap):
@@
-    # Determine the number of columns that fit in the available height.
-    # The algorithm tries to balance the columns, so it may reduce the
-    # column count when the content fits into fewer columns.
-    column_count = max(
-        1,
-        int(math.ceil(content_height / (available_height - column_gap))),
-    )
-
-    # Compute the column width from the remaining space.
-    column_width = (
-        (available_width - (column_count - 1) * column_gap) / column_count
-    )
+    # -----------------------------------------------------------------
+    # Determine the number of columns.
+    #
+    # The CSS specification says that an explicit ``column-count`` (set
+    # via ``columns: N`` or ``column-count: N``) must be respected even
+    # when the content would fit into fewer columns.  The original code
+    # tried to *balance* columns by reducing the column count when the
+    # content height was less than the available height.  This caused the
+    # bug described in the issue: when the content exactly filled one
+    # column and a ``page-break-before`` forced a new page, the algorithm
+    # decided that only one column was needed, ignoring the explicit
+    # ``column-count: 2`` and producing a single narrow column.
+    #
+    # The fix is simple: if the style explicitly defines a column count,
+    # use it unconditionally.  Only when the author did **not** specify a
+    # count do we fall back to the balancing heuristic.
+    # -----------------------------------------------------------------
+
+    # 1. Respect an explicit column‑count from the style.
+    if box.style.column_count is not None:
+        column_count = box.style.column_count
+    else:
+        # 2. No explicit count → fall back to the original balancing logic.
+        column_count = max(
+            1,
+            int(math.ceil(content_height / (available_height - column_gap))),
+        )
+
+    # Compute the column width from the remaining space.
+    column_width = (
+        (available_width - (column_count - 1) * column_gap) / column_count
+    )
*** End of File ***
```

**Explanation of the changes**

* Added a detailed comment describing the bug and the reasoning behind the fix.
* Introduced a guard that checks `box.style.column_count`.  
  * If the author set a column count (as happens with `columns: 2`), we use that value directly.  
  * If no expli

=== TASK godbus__dbus-297 | repo godbus/dbus | go
--- Issue
Prevent encoding errors
As seen in e.g. #244, #240, #205, it's relatively easy for application code to create messages that are ill-formatted semantically, and thus trigger a disconnection by the bus daemon. We should check this directly in library code to prevent one wrong message from terminating the whole connection, which would also result in better error messages:
1. Ensure that message.IsValid covers everything that already is checked in encoder.go
2. Check for valid format for error and signal names
3. Check IsValid in Emit
Method replies are a not-so-nice case because there's no real way to give feedback from the library to the application code here when using Export (without an API break), but just writing an error to stderr (with the option to disable it) would be an alternative.
--- REAL FIX (ground truth)
diff --git a/conn.go b/conn.go
index 1a92b8b..c50bfe3 100644
--- a/conn.go
+++ b/conn.go
@@ -650,9 +650,7 @@ func (conn *Conn) RemoveMatchSignalContext(ctx context.Context, options ...Match
 
 // Signal registers the given channel to be passed all received signal messages.
 //
-// Multiple of these channels can be registered at the same time. The channel is
-// closed if the Conn is closed; it should not be closed by the caller before
-// RemoveSignal was called on it.
+// Multiple of these channels can be registered at the same time.
 //
 // These channels are "overwritten" by Eavesdrop; i.e., if there currently is a
 // channel for eavesdropped messages, this channel receives all signals, and
diff --git a/export.go b/export.go
index 5223347..d3dd9f7 100644
--- a/export.go
+++ b/export.go
@@ -3,6 +3,7 @@ package dbus
 import (
 	"errors"
 	"fmt"
+	"os"
 	"reflect"
 	"strings"
 )
@@ -209,28 +210,23 @@ func (conn *Conn) handleCall(msg *Message) {
 		}
 		reply.Headers[FieldSignature] = MakeVariant(SignatureOf(reply.Body...))
 
-		conn.sendMessageAndIfClosed(reply, nil)
+		if err := reply.IsValid(); err != nil {
+			fmt.Fprintf(os.Stderr, "dbus: dropping invalid reply to %s.%s on obj %s: %s\n", ifaceName, name, path, err)
+		} else {
+			conn.sendMessageAndIfClosed(reply, nil)
+		}
 	}
 }
 
 // Emit emits the given signal on the message bus. The name parameter must be
 // formatted as "interface.member", e.g., "org.freedesktop.DBus.NameLost".
 func (conn *Conn) Emit(path ObjectPath, name string, values ...interface{}) error {
-	if !path.IsValid() {
-		return errors.New("dbus: invalid object path")
-	}
 	i := strings.LastIndex(name, ".")
 	if i == -1 {
 		return errors.New("dbus: invalid method name")
 	}
 	iface := name[:i]
 	member := name[i+1:]
-	if !isValidMember(member) {
-		return errors.New("dbus: invalid method name")
-	}
-	if !isValidInterface(iface) {
-		return errors.New("dbus: invalid interface name")
-	}
 	msg := new(Message)
 	msg.Type = TypeSignal
 	msg.Headers = make(map[HeaderField]Variant)
@@ -241,6 +237,9 @@ func (conn *Conn) Emit(path ObjectPath, name string, values ...interface{}) erro
 	if len(values) > 0 {
 		msg.Headers[FieldSignature] = MakeVariant(SignatureOf(values...))
 	}
+	if err := msg.IsValid(); err != nil {
+		return err
+	}
 
 	var closed bool
 	conn.sendMessageAndIfClosed(msg, func() {
diff --git a/message.go b/message.go
index 16693eb..bdf43fd 100644
--- a/message.go
+++ b/message.go
@@ -208,7 +208,7 @@ func DecodeMessageWithFDs(rd io.Reader, fds []int) (msg *Message, err error) {
 // The possibly returned error can be an error of the underlying reader, an
 // InvalidMessageError or a FormatError.
 func DecodeMessage(rd io.Reader) (msg *Message, err error) {
-	return DecodeMessageWithFDs(rd, make([]int, 0));
+	return DecodeMessageWithFDs(rd, make([]int, 0))
 }
 
 type nullwriter struct{}
@@ -227,8 +227,8 @@ func (msg *Message) CountFds() (int, error) {
 }
 
 func (msg *Message) EncodeToWithFDs(out io.Writer, order
--- Proposal A
## Root cause
`Message.IsValid` does not perform all the checks that the encoder performs before a message is written to the bus.  
Consequently malformed messages (e.g. with an illegal error name, an illegal signal/member name, or other
semantic problems) can reach the encoder, cause it to return an error and the D‑Bus daemon will close the
connection.  
The library also sends messages from `Emit` without any validation, and method‑reply generation in
`Export` silently discards errors, making debugging difficult.

## Fix
* Extend `Message.IsValid` so that it mirrors every validation performed in `encoder.go` (bus name,
  interface name, member name, object path, error name, etc.).
* Add a validation step in `Emit` – reject invalid messages early with a clear error.
* In the method‑reply path (`export.go`) log an error to `stderr` when a generated reply is invalid
  (the library cannot return the error to the caller without an API break).  The logging can be
  disabled via a package‑level flag.

```diff
--- a/message.go
+++ b/message.go
@@
 import (
     "errors"
+    "fmt"
+    "log"
     "strings"
 )
 
 // Message represents a D‑Bus message.
 type Message struct {
@@
 func (m *Message) IsValid() bool {
-    // TODO: implement full validation
-    return true
+    // Basic sanity checks that are also performed by the encoder.
+    // These checks are deliberately exhaustive so that a malformed
+    // message never reaches the encoder and causes a bus disconnection.
+
+    // Message type must be known.
+    switch m.Type {
+    case TypeMethodCall, TypeMethodReturn, TypeError, TypeSignal:
+        // ok
+    default:
+        return false
+    }
+
+    // Path must be a valid object path if present.
+    if m.Path != "" && !IsValidObjectPath(m.Path) {
+        return false
+    }
+
+    // Interface, if set, must be a valid bus name.
+    if m.Interface != "" && !IsValidInterfaceName(m.Interface) {
+        return false
+    }
+
+    // Member (method or signal name) must be a valid member name.
+    if m.Member != "" && !IsValidMemberName(m.Member) {
+        return false
+    }
+
+    // Destination and Sender must be valid bus names if set.
+    if m.Destination != "" && !IsValidBusName(m.Destination) {
+        return false
+    }
+    if m.Sender != "" && !IsValidBusName(m.Sender) {
+        return false
+    }
+
+    // For error messages the error name must be a valid error name.
+    if m.Type == TypeError {
+        if m.ErrorName == "" || !IsValidErrorName(m.ErrorName) {
+            return false
+        }
+    }
+
+    // For signals the member name must be a valid signal name.
+    if m.Type == TypeSignal {
+        if m.Member == "" || !IsValidMemberName(m.Member) {
+            return false
+        }
+    }
+
+    // All checks passed.
+    return true
 }
 
 // String returns a human readable representation of the message.
 func (m *Message) String() string {
@@
--- a/conn.go
+++ b/conn.go
@@
 func (c *Conn) Emit(msg *Message) error {
-    // TODO: add proper validation
-    return c.sendMessage(msg)
+    // Validate the message before sending it.  This prevents malformed
+    // messages from reaching the encoder and causing the bus daemon to
+    // terminate the connection.
+    if !msg.IsValid() {
+        return fmt.Errorf("dbus: cannot emit invalid message: %v", msg)
+    }
+    return c.sendMessage(msg)
 }
 
 // sendMessage writes the message to the underlying transport.
 func (c *Conn) sendMessage(msg *Message)
--- Proposal B
**Root cause**

`Message.IsValid` does not verify all the constraints that the encoder enforces when a message is marshalled.  
Consequences:

* An application can build a `Message` that passes `IsValid` but later fails in `Encode` (e.g. malformed error or signal names, illegal object‑path/interface strings).  
* `Conn.Emit` creates a signal without checking the same invariants, so an invalid signal can be sent and the bus daemon will disconnect the peer.  
* Replies generated by the method‑export machinery (`Export`) are also not checked; when they are malformed the daemon drops the connection and the library has no way to report the problem.

Because the library itself is the only place that knows the exact format required by the D‑Bus spec, the missing validation leads to “encoding errors” that surface as abrupt disconnections.

---

**Fix (unified diff)**  

```diff
--- a/message.go
+++ b/message.go
@@
 type Message struct {
     // ... existing fields ...
 }
 
 // IsValid reports whether the message satisfies the D‑Bus specification.
 // It is used by the encoder and by higher‑level helpers (Emit, Export, …).
 func (m *Message) IsValid() bool {
-    // existing simple checks …
-    return true
+    // Basic type check.
+    switch m.Type {
+    case TypeMethodCall, TypeMethodReturn, TypeError, TypeSignal:
+        // ok
+    default:
+        return false
+    }
+
+    // Destination must be set for method calls.
+    if m.Type == TypeMethodCall && m.Destination == "" {
+        return false
+    }
+
+    // Path must be a valid object path for calls, signals and errors.
+    if (m.Type == TypeMethodCall || m.Type == TypeSignal || m.Type == TypeError) && !IsValidObjectPath(string(m.Path)) {
+        return false
+    }
+
+    // Interface must be a valid interface name for signals and method calls.
+    if (m.Type == TypeMethodCall || m.Type == TypeSignal) && m.Interface != "" && !IsValidInterfaceName(string(m.Interface)) {
+        return false
+    }
+
+    // Member (method, signal or error name) must be a valid member name.
+    if m.Member == "" || !IsValidMemberName(string(m.Member)) {
+        return false
+    }
+
+    // Additional checks for error messages.
+    if m.Type == TypeError {
+        // Error names follow the same rules as interface names.
+        if !IsValidInterfaceName(string(m.Member)) {
+            return false
+        }
+    }
+
+    // Additional checks for signal messages.
+    if m.Type == TypeSignal {
+        // Signal names must be valid member names (no '.' etc.).
+        if !IsValidMemberName(string(m.Member)) {
+            return false
+        }
+    }
+
+    // Signature must match the body length/types.
+    if sig := m.Signature; sig != "" {
+        if !sig.IsValid() {
+            return false
+        }
+        // The encoder already checks body‑signature compatibility, but we
+        // repeat a cheap length check here to catch obvious mismatches early.
+        if len(m.Body) != int(sig.NumFields()) {
+            return false
+        }
+    }
+
+    return true
 }
 
 // Helper validation functions (mirrored from encoder.go)
 func IsValidInterfaceName(s string) bool {
-    // existing implementation …
+    // Must match the D‑Bus interface name grammar:
+    //   [A-Z][a-zA-Z0-9_]* ('.' [A-Z][a-zA-Z0-9_]*)+
+    // The first component may start with a lower‑case letter as well.
+    // Re‑use the same regex that the encoder uses.
+    const ifacePat = `^([a-zA-Z_][a-zA-Z0-9_]*\.

=== TASK postlund__pyatv-978 | repo postlund/pyatv | python
--- Issue
Add retries for heartbeats (MRP)
**What feature would you like?**
When a heartbeat fails, an error is logged and the connection is closed. Generally this is good, but in case the response happens to be delayed long enough (even though nothing is wrong), this is quite pessimistic and it's probably a good idea to make another try before failing. Do not impose an initial delay on the retry though (just retry immediately) as we still want to fail as early as possible in case of an actual problem.

**Describe the solution you'd like**
From the description above: make a retry immediately when a heartbeat fails. Only log error if retry also fails.

**Any other information to share?**
Heartbeat errors seems to happen sporadically and I believe there's not actually a problem in most cases. This is an attempt to recover in those cases, but it's still not a very well tested method so I can't be sure that it works.
--- REAL FIX (ground truth)
diff --git a/pyatv/mrp/protocol.py b/pyatv/mrp/protocol.py
index 9e31abcf..c152b2d3 100644
--- a/pyatv/mrp/protocol.py
+++ b/pyatv/mrp/protocol.py
@@ -14,6 +14,7 @@ from pyatv.mrp.srp import Credentials
 _LOGGER = logging.getLogger(__name__)
 
 HEARTBEAT_INTERVAL = 30
+HEARTBEAT_RETRIES = 1  # One regular attempt + retries
 
 Listener = namedtuple("Listener", "func data")
 OutstandingMessage = namedtuple("OutstandingMessage", "semaphore response")
@@ -23,10 +24,14 @@ async def heartbeat_loop(protocol):
     """Periodically send heartbeat messages to device."""
     _LOGGER.debug("Starting heartbeat loop")
     count = 0
+    attempts = 0
     message = messages.create(protobuf.ProtocolMessage.SEND_COMMAND_MESSAGE)
     while True:
         try:
-            await asyncio.sleep(HEARTBEAT_INTERVAL)
+            # Re-attempts are made with no initial delay to more quickly
+            # recover a failed heartbeat (if possible)
+            if attempts == 0:
+                await asyncio.sleep(HEARTBEAT_INTERVAL)
 
             _LOGGER.debug("Sending periodic heartbeat %d", count)
             await protocol.send_and_receive(message)
@@ -34,11 +39,18 @@ async def heartbeat_loop(protocol):
         except asyncio.CancelledError:
             break
         except Exception:
-            _LOGGER.exception(f"heartbeat {count} failed")
-            protocol.connection.close()
-            break
+            attempts += 1
+            if attempts > HEARTBEAT_RETRIES:
+                _LOGGER.error(f"heartbeat {count} failed after {attempts} tries")
+                protocol.connection.close()
+                break
+            else:
+                _LOGGER.debug(f"heartbeat {count} failed")
         else:
+            attempts = 0
+        finally:
             count += 1
+
     _LOGGER.debug("Stopping heartbeat loop at %d", count)
 
 

--- Proposal A
## Root cause
The MRP (Media Remote Protocol) implementation sends a periodic *heartbeat* to keep the Apple TV connection alive.  
If the heartbeat request raises an exception, the current code logs the error **and immediately closes the connection**.  
Because a delayed response can be harmless, closing the connection on the first failure is too aggressive. The failure should be retried once (without any extra delay) before giving up. The existing implementation does not contain any retry logic, so a transient timeout always tears down the session.

## Fix
Add a single immediate retry for the heartbeat request.  
Only log an error and close the connection if the second attempt also fails.

```diff
--- a/pyatv/mrp/connection.py
+++ b/pyatv/mrp/connection.py
@@
-    async def _heartbeat(self) -> None:
-        """Send a heartbeat to keep the connection alive."""
-        try:
-            await self._protocol.send_heartbeat()
-        except Exception as exc:  # pragma: no cover – exercised via integration tests
-            _LOGGER.error("Heartbeat failed: %s", exc)
-            await self._close()
+    async def _heartbeat(self) -> None:
+        """Send a heartbeat to keep the connection alive.
+
+        The heartbeat is sent once per ``heartbeat_interval``.  If the first
+        attempt fails we immediately retry once before giving up.  This
+        mirrors the behaviour of the official Apple TV client which is tolerant
+        of occasional delayed responses.
+        """
+        async def _do_send() -> None:
+            """Low‑level send that may raise."""
+            await self._protocol.send_heartbeat()
+
+        try:
+            await _do_send()
+        except Exception as first_exc:  # pragma: no cover – exercised via integration tests
+            # First attempt failed – try once more immediately.
+            try:
+                await _do_send()
+            except Exception as second_exc:
+                # Both attempts failed – give up and close the connection.
+                _LOGGER.error("Heartbeat failed (after retry): %s", second_exc)
+                await self._close()
+                # Re‑raise so that any surrounding task can notice the failure.
+                raise
```

### What the patch does
1. **Extracts the actual send operation** into a tiny inner coroutine (`_do_send`).  
2. Calls it once. If it raises, we **immediately retry** by calling it again.  
3. If the retry also raises, we log the error (including the fact that a retry was performed) and close the connection, mirroring the original behaviour.  
4. The exception is re‑raised so that any higher‑level heartbeat loop can stop cleanly.

No other parts of the code need to change because the public contract of `_heartbeat` (raising on failure) stays the same.

## How to verify
1. **Unit test – transient failure**  
   *Mock* `self._protocol.send_heartbeat` so that it raises on the first call and succeeds on the second.  
   Call `await conn._heartbeat()` and assert:
   * No exception is propagated.
   * `_close` is **not** called.
   * The logger does **not** contain an error message.

2. **Unit test – persistent failure**  
   Mock `send_heartbeat` to raise on both calls.  
   Call `await conn._heartbeat()` and assert:
   * An exception is raised.
   * `_close` **is** called exactly once.
   * The logger contains the message “Heartbeat failed (after retry): …”.

3. **Integration test** – run the existing MRP integration test suite (or st
--- Proposal B
## Root cause
The MRP implementation closes the connection as soon as a heartbeat request times‑out.  
When the remote device simply replies a little later (e.g. because of a temporary pause), the single‑shot timeout is too aggressive and results in an unnecessary disconnect. The code never gives the heartbeat a second chance, so a transient delay always leads to a closed session and an error log.

## Fix
Add a single immediate retry for a failed heartbeat.  
* Keep a per‑connection flag (`_heartbeat_retried`) that is cleared after each successful heartbeat.  
* On the first timeout, resend the heartbeat immediately and set the flag.  
* If the heartbeat times‑out again (i.e. the flag is already set) log the error and close the connection as before.  

```diff
--- a/pyatv/mrp/connection.py
+++ b/pyatv/mrp/connection.py
@@
 class MRPConnection:
     def __init__(self, ...):
         ...
-        self._heartbeat_task = None
+        # Heartbeat handling
+        self._heartbeat_task = None
+        # ``True`` when we have already retried a failed heartbeat.
+        # It is cleared after every successful heartbeat.
+        self._heartbeat_retried = False
@@
     async def _send_heartbeat(self) -> None:
         """Send a heartbeat request to the device."""
         try:
@@
         except Exception as exc:  # pragma: no cover – exercised via integration tests
-            _LOGGER.error("Heartbeat failed: %s", exc)
-            await self._close()
+            # First failure – try once more before giving up
+            if not self._heartbeat_retried:
+                _LOGGER.debug("Heartbeat failed (%s), retrying once", exc)
+                self._heartbeat_retried = True
+                # Resend the heartbeat immediately without waiting for the next interval
+                await self._send_heartbeat()
+                return
+
+            # Second failure – give up
+            _LOGGER.error("Heartbeat failed after retry: %s", exc)
+            await self._close()
@@
     async def _handle_heartbeat_response(self, response: mrp_pb2.HeartbeatResponse) -> None:
         """Process a successful heartbeat response."""
-        self._heartbeat_retried = False
+        # Reset retry flag on a successful heartbeat so the next timeout can retry again
+        self._heartbeat_retried = False
         # (existing handling code continues …)
```

### What the patch does
1. **State tracking** – adds `_heartbeat_retried` to remember whether we have already performed a retry for the current heartbeat cycle.
2. **Retry logic** – on the first exception while sending a heartbeat we log at *debug* level, set the retry flag, and immediately call `_send_heartbeat()` again.
3. **Failure handling** – if the second attempt also raises, we log an *error* (as before) and close the connection.
4. **Reset** – when a heartbeat response is successfully received the retry flag is cleared, allowing the next timeout to retry again.

## How to verify
1. **Unit test** – add a test that mocks the transport so the first heartbeat send raises a timeout exception and the second succeeds.  
   *Assert* that the connection stays open and no error is logged, and that `_heartbeat_retried` is cleared after the successful response.

2. **Failure path test** – mock the transport so both the first and the retry raise.  
   *Assert* that an error is logged, `_close()` is called, and the connection is marked closed.

3. **Integration test** – run the existing test‑suite (`pytest 

=== TASK wkeeling__selenium-wire-509 | repo wkeeling/selenium-wire | python
--- Issue
importing uc from seleniumwire ignores proxy option
On newer versions of undetected chromedriver, I need to use this arg ``use_subprocess=True`` else I can't run it without getting a Runtime error. (https://github.com/ultrafunkamsterdam/undetected-chromedriver/issues/432) This argument seems to break the usage of proxies with selenium wire though.

```python
import seleniumwire.undetected_chromedriver.v2 as uc
from seleniumwire import webdriver

chrome_options = webdriver.ChromeOptions()
chrome_options.headless = False
seleniumwire_options = {'proxy': {'https': 'type://host:port',}} 
driver = uc.Chrome(options=chrome_options, seleniumwire_options=seleniumwire_options, use_subprocess=True)

driver.get('https://whatismyipaddress.com/')
```

Ignores passed proxy option and loads website without proxy.
--- REAL FIX (ground truth)
diff --git a/README.rst b/README.rst
index 17e62f7..bb02330 100644
--- a/README.rst
+++ b/README.rst
@@ -694,21 +694,19 @@ If you wish to take advantage of this make sure you have undetected_chromedriver
 
     pip install undetected-chromedriver
 
-Then you can select the version of undetected_chromedriver you want to use by importing ``Chrome`` and ``ChromeOptions`` from the appropriate package.
-
-For undetected_chromedriver version 1:
+Then in your code, import the ``seleniumwire.undetected_chromedriver`` package:
 
 .. code:: python
 
-    from seleniumwire.undetected_chromedriver import Chrome, ChromeOptions
-
-For undetected_chromedriver version 2:
+    import seleniumwire.undetected_chromedriver as uc
 
-.. code:: python
+    chrome_options = uc.ChromeOptions()
 
-    from seleniumwire.undetected_chromedriver.v2 import Chrome, ChromeOptions
+    driver = uc.Chrome(
+        options=chrome_options,
+        seleniumwire_options={}
+    )
 
-See the `undetected_chromedriver docs <https://github.com/ultrafunkamsterdam/undetected-chromedriver>`_ for differences between the two versions.
 
 Certificates
 ~~~~~~~~~~~~
diff --git a/seleniumwire/storage.py b/seleniumwire/storage.py
index 6d1cd66..1be95da 100644
--- a/seleniumwire/storage.py
+++ b/seleniumwire/storage.py
@@ -181,12 +181,9 @@ class RequestStorage:
         request_dir = self._get_request_dir(request_id)
 
         with open(os.path.join(request_dir, 'request'), 'rb') as req:
-            try:
-                request = pickle.load(req)
-            except Exception:
-                # Errors may sometimes occur with unpickling - e.g.
-                # sometimes data hasn't been fully flushed to disk
-                # by the OS by the time we come to unpickle it.
+            request = self._unpickle(req)
+
+            if request is None:
                 return None
 
             ws_messages = self._ws_messages.get(request.id)
@@ -198,19 +195,37 @@ class RequestStorage:
             try:
                 # Attach the response if there is one.
                 with open(os.path.join(request_dir, 'response'), 'rb') as res:
-                    response = pickle.load(res)
-                    request.response = response
-
-                    # The certificate data has been stored on the response but we make
-                    # it available on the request which is a more logical location.
-                    if hasattr(response, 'cert'):
-                        request.cert = response.cert
-                        del response.cert
+                    response = self._unpickle(res)
+
+                    if response is not None:
+                        request.response = response
+
+                        # The certificate data has been stored on the response but we make
+                        # it available on the request which is a more logical location.
+                        if hasattr(response, 'cert'):
+                            request.cert = response.cert
+  
--- Proposal A
## Root cause
`seleniumwire.undetected_chromedriver.v2` builds the `Chrome` driver by delegating to **undetected‑chromedriver**.  
When `use_subprocess=True` the wrapper skips the code that injects Selenium‑Wire’s proxy configuration into the `ChromeOptions`. Consequently the launched Chrome instance never receives the `--proxy-server=…` argument and the Selenium‑Wire proxy is ignored.

## Fix
Add explicit handling of the `proxy` entry in `seleniumwire_options` **before** the driver (or its subprocess) is started, ensuring the proxy argument is always added to the `ChromeOptions` regardless of the `use_subprocess` flag.

```diff
--- a/seleniumwire/undetected_chromedriver/v2.py
+++ b/seleniumwire/undetected_chromedriver/v2.py
@@
-from seleniumwire import webdriver as sw_webdriver
-import undetected_chromedriver as uc
+from seleniumwire import webdriver as sw_webdriver
+import undetected_chromedriver as uc
+import urllib.parse
 
 
 class Chrome(uc.Chrome):
     """
     Wrapper around undetected_chromedriver.Chrome that adds Selenium Wire
     functionality.
     """
 
-    def __init__(self, *args, seleniumwire_options=None, **kwargs):
-        super().__init__(*args, **kwargs)
-        self._seleniumwire_options = seleniumwire_options or {}
-        self._start_proxy()
+    def __init__(self, *args, seleniumwire_options=None, **kwargs):
+        """
+        Initialise the driver, making sure Selenium‑Wire proxy settings are
+        honoured even when ``use_subprocess=True``.
+        """
+        seleniumwire_options = seleniumwire_options or {}
+
+        # ------------------------------------------------------------------
+        # 1️⃣  Ensure the proxy argument is added to ChromeOptions.
+        # ------------------------------------------------------------------
+        proxy_cfg = seleniumwire_options.get("proxy")
+        if proxy_cfg:
+            # Selenium‑Wire supports ``http`` and ``https`` keys – we prefer
+            # https if present, otherwise fall back to http.
+            proxy_url = proxy_cfg.get("https") or proxy_cfg.get("http")
+            if proxy_url:
+                # Normalise the URL – Selenium expects a scheme‑less host:port
+                # when the scheme is already encoded in the argument.
+                parsed = urllib.parse.urlparse(proxy_url)
+                if parsed.scheme:
+                    proxy_arg = f"{parsed.scheme}://{parsed.netloc}"
+                else:
+                    proxy_arg = proxy_url
+
+                # Retrieve (or create) a ChromeOptions instance that will be
+                # passed to the underlying undetected_chromedriver.
+                options = kwargs.get("options") or kwargs.get("chrome_options")
+                if not options:
+                    options = sw_webdriver.ChromeOptions()
+                    # Preserve the original keyword name for downstream code.
+                    kwargs["options"] = options
+
+                # Avoid adding the argument twice if the user already supplied it.
+                proxy_flag = f"--proxy-server={proxy_arg}"
+                if proxy_flag not in options.arguments:
+                    options.add_argument(proxy_flag)
+
+        # ------------------------------------------------------------------
+        # 2️⃣  Store the Selenium‑Wire options and start the internal proxy.
+        # ------------------------------------------------------------------
+        self._seleniumwire_options = seleniumwire_optio
--- Proposal B
## Root cause
`seleniumwire.undetected_chromedriver.v2.Chrome` is a thin wrapper around the regular Selenium‑Wire `Chrome` driver.  
When the wrapper is instantiated with `use_subprocess=True` it follows a special code‑path that delegates the actual browser launch to **undetected‑chromedriver**’s own `Chrome` implementation.  
In that path the wrapper **does not forward** the `seleniumwire_options` (and therefore the proxy configuration) to the base `seleniumwire.webdriver.Chrome` class, nor does it start Selenium‑Wire’s internal MITM proxy.  
Consequently the browser is started without the Selenium‑Wire proxy and the request is sent directly, ignoring the proxy option supplied by the user.

## Fix
Update `seleniumwire/undetected_chromedriver/v2.py` so that:

1. The `seleniumwire_options` argument is always stored and passed to the parent `seleniumwire.webdriver.Chrome` constructor, even when `use_subprocess=True`.
2. The internal Selenium‑Wire proxy is started **before** the subprocess driver is created, and the generated proxy address is injected into the Chrome options that are handed to undetected‑chromedriver.

```diff
--- a/seleniumwire/undetected_chromedriver/v2.py
+++ b/seleniumwire/undetected_chromedriver/v2.py
@@
-class Chrome(seleniumwire.webdriver.Chrome):
-    """
-    Wrapper around seleniumwire's Chrome driver that adds support for
-    undetected_chromedriver's ``use_subprocess`` argument.
-    """
-
-    def __init__(self, *args, seleniumwire_options=None, use_subprocess=False, **kwargs):
-        """
-        Parameters
-        ----------
-        seleniumwire_options : dict, optional
-            Options passed to Selenium Wire.
-        use_subprocess : bool, default False
-            If True, launch Chrome via undetected_chromedriver's subprocess mode.
-        """
-        self.use_subprocess = use_subprocess
-
-        # When ``use_subprocess`` is True we cannot rely on Selenium Wire's
-        # normal proxy injection because the driver is started in a separate
-        # process.  The original implementation simply ignored the proxy
-        # configuration, which caused the issue described in #XXX.
-
-        if use_subprocess:
-            # Initialise the base Selenium Wire driver without proxy handling.
-            super().__init__(*args, seleniumwire_options=seleniumwire_options, **kwargs)
-            # Launch undetected_chromedriver in subprocess mode.
-            self.driver = uc.Chrome(*args, **kwargs, use_subprocess=True)
-        else:
-            # Normal path – let Selenium Wire handle everything.
-            super().__init__(*args, seleniumwire_options=seleniumwire_options, **kwargs)
+class Chrome(seleniumwire.webdriver.Chrome):
+    """
+    Wrapper around Selenium‑Wire's Chrome driver that adds support for
+    undetected_chromedriver's ``use_subprocess`` argument while **preserving**
+    Selenium‑Wire's proxy handling.
+    """
+
+    def __init__(
+        self,
+        *args,\n+        seleniumwire_options: dict | None = None,\n+        use_subprocess: bool = False,\n+        **kwargs,\n+    ):\n+        \"\"\"Create a Chrome driver that works with Selenium‑Wire **and**\n+        undetected‑chromedriver's subprocess mode.\n+\n+        Parameters\n+        ----------\n+        seleniumwire_options:\n+            Options passed to Selenium‑Wire (e.g. ``{\"proxy\": {...}}``).\n+        use_subprocess:\n+            When ``True`` the underlying Chrome instance is started via\n+            

=== TASK feature-engine__feature_engine-794 | repo feature-engine/feature_engine | python
--- Issue
yeo-johnson inverse transform throws an erro
```
InvalidIndexError                         Traceback (most recent call last)
File ~\Documents\Repositories\envs\fe_not\lib\site-packages\pandas\core\series.py:1289, in Series.__setitem__(self, key, value)
   1288 try:
-> 1289     self._set_with_engine(key, value, warn=warn)
   1290 except KeyError:
   1291     # We have a scalar (or for MultiIndex or object-dtype, scalar-like)
   1292     #  key that is not present in self.index.

File ~\Documents\Repositories\envs\fe_not\lib\site-packages\pandas\core\series.py:1361, in Series._set_with_engine(self, key, value, warn)
   1360 def _set_with_engine(self, key, value, warn: bool = True) -> None:
-> 1361     loc = self.index.get_loc(key)
   1363     # this is equivalent to self._values[key] = value

File ~\Documents\Repositories\envs\fe_not\lib\site-packages\pandas\core\indexes\range.py:418, in RangeIndex.get_loc(self, key)
    417     raise KeyError(key)
--> 418 self._check_indexing_error(key)
    419 raise KeyError(key)

File ~\Documents\Repositories\envs\fe_not\lib\site-packages\pandas\core\indexes\base.py:6059, in Index._check_indexing_error(self, key)
   6056 if not is_scalar(key):
   6057     # if key is not a scalar, directly raise an error (the code below
   6058     # would convert to numpy arrays and raise later any way) - GH29926
-> 6059     raise InvalidIndexError(key)

InvalidIndexError: 64      True
682     True
960     True
1384    True
1100
--- REAL FIX (ground truth)
diff --git a/feature_engine/transformation/yeojohnson.py b/feature_engine/transformation/yeojohnson.py
index 5e33c05..f87d099 100644
--- a/feature_engine/transformation/yeojohnson.py
+++ b/feature_engine/transformation/yeojohnson.py
@@ -185,7 +185,7 @@ class YeoJohnsonTransformer(BaseNumericalTransformer):
         return X
 
     def _inverse_transform_series(self, X: pd.Series, lmbda: float) -> pd.Series:
-        x_inv = pd.Series(np.zeros_like(X))
+        x_inv = pd.Series(np.zeros_like(X), index=X.index)
         pos = X >= 0
 
         # when x >= 0

--- Proposal A
## Root cause
`YeoJohnsonTransformer._inverse_transform_series` builds the inverse‑transformed array `x_inv` as a **pandas Series** (or copies the input Series) and then indexes it with a **boolean pandas Series** `pos = X < 0`.  
Because `x_inv` is created with the default `RangeIndex` while `pos` keeps the original index of `X`, the two indexes do not match. When pandas tries to align the boolean indexer it raises  

```
InvalidIndexError / IndexingError: Unalignable boolean Series provided as indexer
```

Consequently `inverse_transform` fails for any column that contains negative values.

## Fix
Rewrite `_inverse_transform_series` so that all arithmetic is performed on **NumPy arrays** and the returned object is a pandas Series that re‑uses the original index. This guarantees that the boolean mask and the target array are aligned.

```diff
--- a/feature_engine/transformation/yeojohnson.py
+++ b/feature_engine/transformation/yeojohnson.py
@@
-    def _inverse_transform_series(self, X: pd.Series, lmbda: float) -> pd.Series:
-        \"\"\"Inverse transform a single column.\n-
-
-        Parameters
-        ----------
-        X : pandas.Series
-            Series to be inverse transformed.
-        lmbda : float
-            Lambda value used for the transformation of the column.\n-
-
-        Returns
-        -------
-        pandas.Series
-            Inverse transformed series.\n-
-        \"\"\n-
-        # boolean mask for values < 0 (the Yeo‑Johnson formula is different for\n-        # negative and non‑negative values)\n-        pos = X < 0\n-\n-        # initialise array that will hold the inverse transformed values\n-        x_inv = X.copy()\n-\n-        if lmbda == 0:\n-            # when lambda == 0 the transformation is log(x + 1)\n-            x_inv[pos] = np.exp(X[pos]) - 1\n-        else:\n-            # lambda != 0\n-            x_inv[pos] = np.power(X[pos] * lmbda + 1, 1 / lmbda) - 1\n-\n-        # when x >= 0 the formula is different\n-        if lmbda != 2:\n-            # lambda == 2 corresponds to the identity function for non‑negative\n-            # values, therefore we skip the computation in that case\n-            pos = ~pos\n-            if lmbda == 0:\n-                x_inv[pos] = np.exp(X[pos]) - 1\n-            else:\n-                x_inv[pos] = np.power(X[pos] * lmbda + 1, 1 / lmbda) - 1\n-\n-        return x_inv\n+    def _inverse_transform_series(self, X: pd.Series, lmbda: float) -> pd.Series:\n+        \"\"\"Inverse‑transform a single column.\n+\n+        The original implementation mixed pandas indexing with NumPy indexing:\n+        it created a pandas Series (or a copy of ``X``) and then tried to index it\n+        with a boolean *pandas* Series that carried the original index.  Because\n+        the temporary Series used a default ``RangeIndex`` the two indexes did\n+        not align, leading to the ``InvalidIndexError`` observed in the issue.\n+\n+        The fix performs all calculations on plain NumPy arrays, using the\n+        boolean mask ``pos`` as a NumPy boolean array (``pos.values``).  After the\n+        inverse transformation is complete we wrap the result back into a\n+        pandas Series that preserves the original index, guaranteeing that the\n+        caller receives a correctly aligned column.\n+\n+        Parameters\n+        ----------\n+        X : pandas.Series\n+            Series to be inverse transformed.\n+        lmbda : float\n+            Lambda value used for the 
--- Proposal B
## Root cause
`YeoJohnsonTransformer._inverse_transform_series` builds a **boolean mask** (`pos`) from the input Series `X` and then tries to assign the inverse‑transformed values back to another Series (`x_inv`) using that mask:

```python
x_inv[pos] = np.power(X[pos] * lmbda + 1, 1 / lmbda) - 1
```

`x_inv` is created from `X.values` (or similar) **without preserving the original index**. Consequently the boolean mask `pos` (which carries the original index) does not align with the index of `x_inv`. Pandas raises an `InvalidIndexError / IndexingError` because the boolean Series used as an indexer cannot be aligned with the target Series.

The bug surfaces only when `inverse_transform` is called, because the forward transform works on NumPy arrays, while the inverse step mixes NumPy and pandas indexing.

## Fix
Rewrite `_inverse_transform_series` so that all operations are performed on NumPy arrays and the resulting Series is rebuilt with the original index. This guarantees that the boolean mask and the target array are perfectly aligned and removes the pandas‑level indexing error.

```diff
--- a/feature_engine/transformation/yeojohnson.py
+++ b/feature_engine/transformation/yeojohnson.py
@@
-    def _inverse_transform_series(self, X: pd.Series, lmbda: float) -> pd.Series:
-        \"\"\"Inverse transform a single pandas Series.\n-
-
-        Parameters\n-
-        ----------\n-
-        X : pandas.Series\n-
-            Series to be inverse transformed.\n-
-        lmbda : float\n-
-            Lambda value used for the transformation.\n-
-
-        Returns\n-
-        -------\n-
-        pandas.Series\n-
-            Inverse transformed Series.\n-
-        \"\"\"\n-
-        # initialise an empty array to store the inverse transformed values\n-        x_inv = np.empty_like(X)\n-\n-        # when x >= 0\n-        pos = X >= 0\n-        if lmbda == 0:\n-            x_inv[pos] = np.exp(X[pos]) - 1\n-        else:  # lmbda != 0\n-            x_inv[pos] = np.power(X[pos] * lmbda + 1, 1 / lmbda) - 1\n-\n-        # when x < 0\n-        neg = ~pos\n-        if lmbda == 2:\n-            x_inv[neg] = np.log(X[neg] + 1)\n-        else:  # lmbda != 2\n-            x_inv[neg] = np.power(X[neg] * (2 - lmbda) + 1, 1 / (2 - lmbda)) - 1\n-\n-        # return a pandas Series with the original index and name\n-        return pd.Series(x_inv, index=X.index, name=X.name)\n+    def _inverse_transform_series(self, X: pd.Series, lmbda: float) -> pd.Series:\n+        \"\"\"Inverse‑transform a single pandas Series.\n+\n+        The original implementation mixed NumPy arrays and pandas indexing,\n+        which caused a mis‑alignment between the boolean mask (which kept the\n+        original index) and the target Series (which lost its index).  The\n+        updated version works entirely on NumPy arrays and rebuilds the\n+        Series at the end, guaranteeing index alignment.\n+\n+        Parameters\n+        ----------\n+        X : pandas.Series\n+            Series to be inverse transformed.\n+        lmbda : float\n+            Lambda value used for the forward Yeo‑Johnson transformation.\n+\n+        Returns\n+        -------\n+        pandas.Series\n+            Inverse‑transformed Series with the same index and name as ``X``.\n+        \"\"\"\n+\n+        # Work on the underlying NumPy array to avoid pandas alignment issues\n+        x = X.values.astype(float)\n+        x_inv = np.empty_like(x)\n+\n+        # Boolean mask for the two regions defined

=== TASK Adyen__adyen-python-api-library-102 | repo Adyen/adyen-python-api-library | python
--- Issue
Util generate_hpp_sig() value and hmac do not correspond
**Python version**: 2.7.16
**Library version**: 2.1.0 (and tested too with future 2.2.0)
**Description**
TEST ENVIRONMENT

Implemented webhook for Adyen notifications (https://docs.adyen.com/development-resources/notifications) and using Test Configuration form in Adyen backend.

Passed JSON received data from Adyen to generate_hpp_sig() function according to test here : https://github.com/Adyen/adyen-python-api-library/blob/develop/test/UtilTest.py#L13

The generated hmac does not correspond to the addtionnalData['hmacSignature'].


Even tested with future 2.2.0 function is_valid_hmac, returns False.


--- REAL FIX (ground truth)
diff --git a/Adyen/util.py b/Adyen/util.py
index 580ff31..6156876 100644
--- a/Adyen/util.py
+++ b/Adyen/util.py
@@ -44,3 +44,53 @@ def is_valid_hmac(dict_object, hmac_key):
             merchant_sign = generate_hpp_sig(dict_object, hmac_key)
             merchant_sign_str = merchant_sign.decode("utf-8")
             return merchant_sign_str == expected_sign
+
+
+def generate_notification_sig(dict_object, hmac_key):
+    if 'issuerId' in dict_object:
+        if dict_object['issuerId'] == "":
+            del dict_object['issuerId']
+
+    if not isinstance(dict_object, dict):
+        raise ValueError("Must Provide dictionary object")
+
+    def escape_val(val):
+        if isinstance(val, int):
+            return val
+        return val.replace('\\', '\\\\').replace(':', '\\:')
+
+    hmac_key = binascii.a2b_hex(hmac_key)
+
+    request_dict = dict(dict_object)
+    request_dict['value'] = request_dict['amount']['value']
+    request_dict['currency'] = request_dict['amount']['currency']
+
+    element_orders = [
+        'pspReference',
+        'originalReference',
+        'merchantAccountCode',
+        'merchantReference',
+        'value',
+        'currency',
+        'eventCode',
+        'success',
+    ]
+
+    signing_string = ':'.join(
+        map(escape_val, map(str, (
+            request_dict.get(element, '') for element in element_orders))))
+
+    hm = hmac.new(hmac_key, signing_string.encode('utf-8'), hashlib.sha256)
+    return base64.b64encode(hm.digest())
+
+
+def is_valid_hmac_notification(dict_object, hmac_key):
+    if 'additionalData' in dict_object:
+        if dict_object['additionalData']['hmacSignature'] == "":
+            raise ValueError("Must Provide hmacSignature in additionalData")
+        else:
+            expected_sign = dict_object['additionalData']['hmacSignature']
+            del dict_object['additionalData']
+            merchant_sign = generate_notification_sig(dict_object, hmac_key)
+            merchant_sign_str = merchant_sign.decode("utf-8")
+            return merchant_sign_str == expected_sign

--- Proposal A
## Root cause
`generate_hpp_sig()` built the signing string from the dictionary **in the original insertion order** and included empty values.  
According to Adyen’s HPP specification the parameters must be:

1. **Sorted alphabetically by key** (case‑sensitive).  
2. **Empty values must be ignored** (they must not appear in the string).  
3. The key `merchantSig` must never be part of the string.  

Because the implementation used the raw `dict.items()` order, the generated string differed from the one Adyen uses to calculate the HMAC, so the resulting signature never matched the `hmacSignature` sent in the notification.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/adyen/util.py
+++ b/adyen/util.py
@@
-import base64
-import hashlib
-import hmac
-
-def generate_hpp_sig(params, secret):
-    """
-    Generate an HPP signature.
-
-    :param params: dict of parameters that will be sent to HPP
-    :param secret: secret key (string)
-    :return: base64 encoded HMAC SHA256 signature
-    """
-    # NOTE: the original implementation built the signing string from the
-    # dictionary order, which is not guaranteed and caused mismatched signatures.
-    # It also included empty values and the merchantSig key itself.
-    # The Adyen spec requires the parameters to be sorted alphabetically,
-    # empty values ignored and merchantSig excluded.
-
-    # Remove the merchantSig key if present and any empty values
-    signing_params = {k: v for k, v in params.items() if k != 'merchantSig' and v}
-
-    # Build the signing string: each line is "key:value"
-    signing_string = ''
-    for key, value in signing_params.items():
-        signing_string += f'{key}:{value}\n'
-
-    # Trim the trailing newline
-    signing_string = signing_string.rstrip('\\n')
-
-    # Compute HMAC SHA256 and return base64 encoded result
-    hm = hmac.new(secret.encode('utf-8'), signing_string.encode('utf-8'), hashlib.sha256)
-    return base64.b64encode(hm.digest()).decode('utf-8')
+import base64
+import hashlib
+import hmac
+
+def generate_hpp_sig(params, secret):
+    """
+    Generate an HPP (Hosted Payment Page) signature according to Adyen’s spec.
+
+    The signature is a Base64‑encoded HMAC‑SHA256 of a *canonical* string that
+    is built from the request parameters:
+
+    * All parameters **except** ``merchantSig`` are considered.
+    * Parameters with an empty value (``None`` or empty string) are ignored.
+    * Remaining keys are sorted **alphabetically** (case‑sensitive).
+    * The canonical string consists of ``key:value`` pairs separated by a
+      newline character (``\\n``).  No trailing newline is added.
+    * The secret key is UTF‑8 encoded before being used as the HMAC key.
+
+    :param params: ``dict`` of parameters that will be sent to the HPP.
+    :param secret: The HMAC secret provided by Adyen (string).
+    :return: Base64‑encoded HMAC‑SHA256 signature (unicode string).
+    """
+    # 1️⃣ Remove ``merchantSig`` and any empty values.
+    signing_params = {
+        k: v for k, v in params.items()
+        if k != 'merchantSig' and v not in (None, '')
+    }
+
+    # 2️⃣ Sort the keys alphabetically as required by the spec.
+    sorted_keys = sorted(signing_params.keys())
+
+    # 3️⃣ Build the canonical signing string.
+    #    Each line is ``key:value`` – the final line must **not** end with ``\\n``.
+    signing_parts = [
+        f'{key}:{signing_params[key]}' for key in sorted_keys
+    ]
+    
--- Proposal B
## Root cause  

`generate_hpp_sig()` (and the related `is_valid_hmac()`) built the string that is signed and the secret key using the default *text* type.  
In Python 2 the default type is `unicode` for values that come from JSON, while the HMAC routine expects **bytes**.  
The function therefore signed a *unicode* string while the secret key was a *str* (or vice‑versa).  
The resulting binary HMAC differed from the one that Adyen generates, so the signature never matched the `additionalData['hmacSignature']` sent in the notification.

## Fix  

```diff
--- a/adyen/util.py
+++ b/adyen/util.py
@@
-def generate_hpp_sig(params, secret):
-    """
-    Generate an HPP signature.
-
-    :param params: dict of parameters that will be sent to the HPP page
-    :param secret: the HPP secret (skin secret)
-    :return: base64 encoded HMAC SHA256 signature
-    """
-    # sort the parameters alphabetically and concatenate the values with ':'
-    signing_string = ':'.join([str(params[k]) for k in sorted(params)])
-    hmac_obj = hmac.new(secret, signing_string, hashlib.sha256)
-    return base64.b64encode(hmac_obj.digest())
+def _to_bytes(value):
+    """
+    Helper that guarantees a *bytes* object on both Python 2 and Python 3.
+    """
+    if isinstance(value, bytes):
+        return value
+    # ``unicode`` on Python 2, ``str`` on Python 3
+    return value.encode('utf-8')
+
+
+def generate_hpp_sig(params, secret):
+    """
+    Generate an HPP signature.
+
+    The HPP signature is a Base64‑encoded HMAC‑SHA256 of the concatenated
+    parameter values (sorted alphabetically) using the *skin secret* as key.
+
+    The original implementation mixed ``unicode`` and ``str`` objects which
+    caused an incorrect binary HMAC on Python 2.  This version normalises
+    everything to UTF‑8 encoded *bytes* before feeding it to ``hmac``.
+
+    :param params: dict of parameters that will be sent to the HPP page
+    :param secret: the HPP secret (skin secret)
+    :return: base64 encoded HMAC SHA256 signature (as a ``str``)
+    """
+    # 1️⃣  Sort the keys alphabetically (as required by the spec)
+    sorted_keys = sorted(params)
+
+    # 2️⃣  Build the signing string – values are converted to ``str`` first
+    #     (to keep the behaviour identical to the reference implementation)
+    signing_string = ':'.join([str(params[k]) for k in sorted_keys])
+
+    # 3️⃣  Convert both the signing string and the secret to ``bytes``.
+    signing_bytes = _to_bytes(signing_string)
+    secret_bytes = _to_bytes(secret)
+
+    # 4️⃣  Compute the HMAC‑SHA256 and Base64‑encode the result.
+    hmac_obj = hmac.new(secret_bytes, signing_bytes, hashlib.sha256)
+    # ``base64.b64encode`` returns ``bytes`` on both Py2 and Py3 – decode to ``str``.
+    return base64.b64encode(hmac_obj.digest()).decode('utf-8')
 
 
-def is_valid_hmac(params, secret, hmac_to_check):
-    """
-    Validate an HMAC signature.
-
-    :param params: dict of parameters received from the HPP page
-    :param secret: the HPP secret (skin secret)
-    :param hmac_to_check: the HMAC signature to validate
-    :return: True if the signature is valid, False otherwise
-    """
-    generated_hmac = generate_hpp_sig(params, secret)
-    return generated_hmac == hmac_to_check
+def is_valid_hmac(params, secret, hmac_to_check):
+    """
+    Validate an HMAC signature generated by ``generate_hpp_sig``.
+
+    The function now uses the same byte‑normalisation logic as the generator,
+    guaranteeing t

=== TASK pre-commit__pre-commit-hooks-575 | repo pre-commit/pre-commit-hooks | python
--- Issue
check-case-conflict doesn't notice directory name conflicts
```
$ git status
On branch master

Initial commit

Changes to be committed:
  (use "git rm --cached <file>..." to unstage)

    new file:   TEST/foo
    new file:   test/bar

$ git commit -m "Test"
Check for case conflicts...................................................................................Passed
[master (root-commit) 6931ba5] Test
 2 files changed, 0 insertions(+), 0 deletions(-)
 create mode 100644 TEST/foo
 create mode 100644 test/bar

$ cat .pre-commit-config.yaml
- repo: git://github.com/pre-commit/pre-commit-hooks
  sha: '0d88124ef6343fbbc6c9d2872853f73546c98a3f'
  hooks:
      - id: check-case-conflict
```

A conflict such as `Test/foo` and `test/foo` is caught though.

--- REAL FIX (ground truth)
diff --git a/pre_commit_hooks/check_case_conflict.py b/pre_commit_hooks/check_case_conflict.py
index 6b8ba82..024c1c3 100644
--- a/pre_commit_hooks/check_case_conflict.py
+++ b/pre_commit_hooks/check_case_conflict.py
@@ -1,5 +1,7 @@
 import argparse
+import os.path
 from typing import Iterable
+from typing import Iterator
 from typing import Optional
 from typing import Sequence
 from typing import Set
@@ -12,9 +14,22 @@ def lower_set(iterable: Iterable[str]) -> Set[str]:
     return {x.lower() for x in iterable}
 
 
+def parents(file: str) -> Iterator[str]:
+    file = os.path.dirname(file)
+    while file:
+        yield file
+        file = os.path.dirname(file)
+
+
+def directories_for(files: Set[str]) -> Set[str]:
+    return {parent for file in files for parent in parents(file)}
+
+
 def find_conflicting_filenames(filenames: Sequence[str]) -> int:
     repo_files = set(cmd_output('git', 'ls-files').splitlines())
+    repo_files |= directories_for(repo_files)
     relevant_files = set(filenames) | added_files()
+    relevant_files |= directories_for(relevant_files)
     repo_files -= relevant_files
     retv = 0
 

--- Proposal A
## Root cause
`check-case-conflict` only inspects the list of **files** staged for commit (the output of `git ls-files`).  
Directory names are never examined, so two directories that differ only by case (e.g., `test/` vs `Test/`) are not considered a conflict. Consequently a repository can be committed with a case‑conflict that only involves directories, which will break on case‑insensitive file systems.

## Fix
Add the missing directory checks. For every staged file we also generate all of its parent directories and run the same case‑insensitive duplicate detection on the combined set of file‑ and directory‑paths.

```diff
--- a/pre_commit_hooks/check_case_conflict.py
+++ b/pre_commit_hooks/check_case_conflict.py
@@
 import sys
 import subprocess
@@
 def main():
-    # Get the list of staged files.
-    proc = subprocess.run(
-        ["git", "ls-files", "--stage", "--"],
-        stdout=subprocess.PIPE,
-        universal_newlines=True,
-        check=True,
-    )
-    files = [line.split("\t", 1)[1].strip() for line in proc.stdout.splitlines()]
-
-    # Detect case‑conflicts.
-    lowered = {}
-    for path in files:
-        norm = os.path.normcase(path)
-        if norm in lowered and lowered[norm] != path:
-            print(
-                f"Case conflict detected: '{path}' and '{lowered[norm]}' refer to the same "
-                "path on a case‑insensitive filesystem.",
-                file=sys.stderr,
-            )
-            sys.exit(1)
-        lowered[norm] = path
-
-    sys.exit(0)
+    # ---------------------------------------------------------------------\n
+    # Gather staged *files*.\n
+    # ---------------------------------------------------------------------\n
+    proc = subprocess.run(\n
+        [\"git\", \"ls-files\", \"--stage\", \"--\"],\n
+        stdout=subprocess.PIPE,\n
+        universal_newlines=True,\n
+        check=True,\n
+    )\n
+    files = [line.split(\"\\t\", 1)[1].strip() for line in proc.stdout.splitlines()]\n+\n+    # ---------------------------------------------------------------------\n+    # Build a collection that contains every file *and* every directory that\n+    # appears in the path of a staged file.  This allows us to detect case\n+    # conflicts that involve directories (e.g. \"test/\" vs \"Test/\") as well as\n+    # file‑vs‑directory conflicts.\n+    # ---------------------------------------------------------------------\n+    paths_to_check: list[str] = []\n+    for f in files:\n+        # Add the file itself.\n+        paths_to_check.append(f)\n+        # Add each parent directory.\n+        parts = f.split(os.sep)\n+        for i in range(1, len(parts)):\n+            dir_path = os.sep.join(parts[:i])\n+            paths_to_check.append(dir_path)\n+\n+    # ---------------------------------------------------------------------\n+    # Detect case‑conflicts.\n+    # ---------------------------------------------------------------------\n+    lowered: dict[str, str] = {}\n+    for path in paths_to_check:\n+        # ``normcase`` normalises case on Windows and lower‑cases on POSIX –\n+        # exactly what we need for a case‑insensitive comparison.\n+        norm = os.path.normcase(path)\n+        if norm in lowered and lowered[norm] != path:\n+            print(\n+                f\"Case conflict detected: '{path}' and '{lowered[norm]}' refer to the same \"\n+                \"path on a case‑insensitive filesystem.\",\n+                file=sys.stderr,\n+            )\n
--- Proposal B
## Root cause
`check-case-conflict` only compares the *full* file paths case‑insensitively.  
If two files live in different directories that only differ by case (e.g. `TEST/foo` vs `test/bar`), their lower‑cased full paths (`test/foo` and `test/bar`) are distinct, so the hook does not see a conflict.  
The hook never inspects the directory components themselves, therefore it misses directory‑name conflicts.

## Fix
Add a second pass that records every directory prefix of each staged file, lower‑cases it and checks for duplicates with a different original case.

```diff
--- a/pre_commit_hooks/check_case_conflict.py
+++ b/pre_commit_hooks/check_case_conflict.py
@@
 from __future__ import annotations
 
 import sys
 from pathlib import Path
@@
 def main(argv: list[str] | None = None) -> int:
-    files = list(_git_ls_files())
-    lowered = {}
-    for f in files:
-        lc = f.lower()
-        if lc in lowered and lowered[lc] != f:
-            print(f'Case conflict: {lowered[lc]} vs {f}')
-            return 1
-        lowered[lc] = f
-
-    return 0
+    """
+    Detect case‑conflicts among staged files **and** among directory names.
+
+    The original implementation only compared the full file paths.  This
+    missed cases where two directories differ only by case (e.g.
+    ``TEST/foo`` vs ``test/bar``).  The fix adds a second pass that walks
+    each file's parent directories, lower‑cases each directory prefix and
+    checks for duplicate case‑insensitive entries with a different original
+    spelling.
+    """
+    files = list(_git_ls_files())
+
+    # ------------------------------------------------------------------
+    # 1. Detect conflicts on the full file paths (original behaviour)
+    # ------------------------------------------------------------------
+    lowered: dict[str, str] = {}
+    for f in files:
+        lc = f.lower()
+        if lc in lowered and lowered[lc] != f:
+            print(f'Case conflict: {lowered[lc]} vs {f}')
+            return 1
+        lowered[lc] = f
+
+    # ------------------------------------------------------------------
+    # 2. Detect conflicts on directory names (new behaviour)
+    # ------------------------------------------------------------------
+    dir_lowered: dict[str, str] = {}
+    for f in files:
+        # Split the path using '/' – pre‑commit works with POSIX‑style paths
+        parts = f.split('/')
+        # Walk each directory prefix (exclude the file itself)
+        for i in range(1, len(parts)):
+            dir_path = '/'.join(parts[:i])
+            lc_dir = dir_path.lower()
+            if lc_dir in dir_lowered and dir_lowered[lc_dir] != dir_path:
+                print(
+                    f'Case conflict in directory name: '
+                    f'{dir_lowered[lc_dir]} vs {dir_path}'
+                )
+                return 1
+            dir_lowered[lc_dir] = dir_path
+
+    return 0
*** End of File ***
```

## How to verify
1. **Create a temporary repo**  

   ```bash
   mkdir case-conflict-test && cd case-conflict-test
   git init
   ```

2. **Add the pre‑commit configuration**  

   ```yaml
   # .pre-commit-config.yaml
   repos:
     - repo: https://github.com/pre-commit/pre-commit-hooks
       rev: v4.5.0   # or any recent tag
       hooks:
         - id: check-case-conflict
   ```

3. **Install the hook**

   ```bash
   pre-commit install
   ```

4. **Create the conflicting files**

   ```bash
   mkdir TEST test
   touch TEST/foo test/bar
   gi

=== TASK duartegroup__autodE-77 | repo duartegroup/autodE | python
--- Issue
NCIComplex conformers are not generated correctly
**Describe the bug**
<!-- A clear and concise description of what the bug is. -->
NCI complex conformers are all generated with the same name, which means optimisations skip over all but the first one. The documentation water trimer works, but for other more complex surfaces this is not expected to.

**To Reproduce**
<!-- Steps to reproduce the behaviour: code with output -->

```
import autode as ade
from autode.species import NCIComplex

ade.Config.num_complex_sphere_points = 5
ade.Config.num_complex_random_rotations = 3

water = ade.Molecule(name='water', smiles='O')

trimer = NCIComplex(water, water, water, name='water_trimer')
trimer.find_lowest_energy_conformer(lmethod=ade.methods.XTB())
trimer.print_xyz_file()
```

The conformers directory only contains a single XTB output file

**Expected behavior**
<!-- A clear and concise description of what you expected to happen. -->

**Environment**
<!-- please complete the following information. -->
 - OS: CentOS
 - Version: v. 1.1.0

--- REAL FIX (ground truth)
diff --git a/autode/conformers/conformers.py b/autode/conformers/conformers.py
index ef46de0..cda456e 100644
--- a/autode/conformers/conformers.py
+++ b/autode/conformers/conformers.py
@@ -147,7 +147,7 @@ class Conformers(list):
             if any(calc_heavy_atom_rmsd(conf.atoms, other.atoms) < rmsd_tol
                    for o_idx, other in enumerate(self) if o_idx != idx):
 
-                logger.info(f'Conformer {idx} was close in geometry to at'
+                logger.info(f'Conformer {idx} was close in geometry to at '
                             f'least one other - removing')
 
                 del self[idx]
diff --git a/autode/species/complex.py b/autode/species/complex.py
index a67efc8..31f7a66 100644
--- a/autode/species/complex.py
+++ b/autode/species/complex.py
@@ -166,7 +166,7 @@ class Complex(Species):
 
             for points in iterprod(points_on_sphere, repeat=n-1):
 
-                conf = Conformer(species=self, name=f'{self.name}_conf{n}')
+                conf = Conformer(species=self, name=f'{self.name}_conf{m}')
                 conf.atoms = get_complex_conformer_atoms(self._molecules,
                                                          rotations,
                                                          points)
diff --git a/doc/common/water_trimer.py b/doc/common/water_trimer.py
index 728b945..22aaea9 100644
--- a/doc/common/water_trimer.py
+++ b/doc/common/water_trimer.py
@@ -1,12 +1,12 @@
-from autode import Molecule, Config
+import autode as ade
 from autode.species import NCIComplex
-from autode.wrappers.XTB import xtb
 
-Config.num_complex_sphere_points = 5
-Config.num_complex_random_rotations = 3
+ade.Config.num_complex_sphere_points = 5
+ade.Config.num_complex_random_rotations = 3
+xtb = ade.methods.XTB()
 
 # Make a water molecule and optimise at the XTB level
-water = Molecule(name='water', smiles='O')
+water = ade.Molecule(name='water', smiles='O')
 water.optimise(method=xtb)
 
 # Make the NCI complex and find the lowest energy structure

--- Proposal A
## Root cause
`NCIComplex` creates all generated conformers with the **same** `name` attribute (the name of the parent complex).  
When the optimisation routine writes the XYZ/trajectory files it uses this `name` to build the output filename, so every conformer overwrites the previous one. Consequently only a single XTB output file appears in the conformers directory and the optimiser effectively skips all but the first conformer.

## Fix
Give each conformer a unique name (e.g. `<complex_name>_conf0`, `<complex_name>_conf1`, …) before the optimisation/IO steps are performed.

```diff
--- a/autode/species/nci_complex.py
+++ b/autode/species/nci_complex.py
@@
     def find_lowest_energy_conformer(self, lmethod, **kwargs):
         """
         Generate a set of conformers for the complex and optimise each
         with the supplied low‑level method. The conformer with the lowest
         energy is retained as the representative structure.
         """
-        conformers = self._generate_conformers()
-
-        # optimise each conformer and keep the lowest‑energy one
-        lowest = None
-        lowest_e = float('inf')
-
-        for conformer in conformers:
-            # run the low‑level optimisation
-            conformer.optimise(lmethod, **kwargs)
-
-            if conformer.energy < lowest_e:
-                lowest_e = conformer.energy
-                lowest = conformer
-
-        # replace the complex geometry with the best conformer
-        if lowest is not None:
-            self.coordinates = lowest.coordinates
-            self.energy = lowest.energy
+        # -----------------------------------------------------------------
+        # NOTE: each generated conformer previously inherited the *same*
+        # name as the parent complex, causing all output files to be written
+        # to the identical path and overwriting one another.  We now assign
+        # a unique name to every conformer before any optimisation or I/O
+        # takes place.
+        # -----------------------------------------------------------------
+        conformers = self._generate_conformers()
+
+        # optimise each conformer and keep the lowest‑energy one
+        lowest = None
+        lowest_e = float('inf')
+
+        for i, conformer in enumerate(conformers):
+            # give a unique name – this is used when writing XYZ/trajectory
+            # files and when storing the conformer in the internal list.
+            conformer.name = f"{self.name}_conf{i}"
+
+            # run the low‑level optimisation
+            conformer.optimise(lmethod, **kwargs)
+
+            if conformer.energy < lowest_e:
+                lowest_e = conformer.energy
+                lowest = conformer
+
+        # replace the complex geometry with the best conformer
+        if lowest is not None:
+            self.coordinates = lowest.coordinates
+            self.energy = lowest.energy
```

### What the patch does
1. **Enumerates** the generated conformers (`for i, conformer in enumerate(conformers):`).  
2. **Assigns** a unique name to each conformer using the parent complex name plus a `_conf{i}` suffix.  
3. Keeps the rest of the optimisation logic unchanged.  
4. The unique name propagates to all downstream file‑writing utilities (e.g. `print_xyz_file`) because they rely on the `name` attribute.

## How to verify
1. **Run the reproducing script** after installing the patched version:

   ```python
   import autode as ade
   from autode.species imp
--- Proposal B
## Root cause
`NCIComplex` creates all generated conformers with the **same molecule name** (the name of the parent complex).  
When the optimisation routine writes the XTB input/output files it uses this name to build the
filenames, so every conformer overwrites the previous one.  
Consequently only a single XTB output file is left in the *conformers* directory and the
optimisation loop effectively sees only the first conformer.

The bug lives in the method that builds the conformer objects – it never appends a unique
identifier (e.g. an index) to the molecule name before the files are written.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/autode/species/nci_complex.py
+++ b/autode/species/nci_complex.py
@@
 class NCIComplex(Complex):
@@
     def _generate_conformers(self) -> List[Molecule]:
         """
         Generate a set of conformers for the NCI complex by sampling points on a
         sphere around the centre of mass and applying random rotations.
@@
-        conformers = []
-        for i in range(Config.num_complex_random_rotations):
-            # generate a random rotation matrix
-            rot = random_rotation_matrix()
-            for point in sphere_points:
-                # translate the second fragment to the sampled point and rotate
-                frag2 = self.fragments[1].copy()
-                frag2.translate(point)
-                frag2.rotate(rot)
-
-                # build the combined molecule
-                mol = self._combine_fragments(self.fragments[0], frag2)
-                mol.name = self.name          # <-- bug: same name for every conformer
-                conformers.append(mol)
-
-        return conformers
+        conformers = []
+        # Counter used to give each conformer a unique name / file stem
+        conf_counter = 0
+
+        for i in range(Config.num_complex_random_rotations):
+            # generate a random rotation matrix
+            rot = random_rotation_matrix()
+            for point in sphere_points:
+                # translate the second fragment to the sampled point and rotate
+                frag2 = self.fragments[1].copy()
+                frag2.translate(point)
+                frag2.rotate(rot)
+
+                # build the combined molecule
+                mol = self._combine_fragments(self.fragments[0], frag2)
+
+                # -----------------------------------------------------------------
+                # **Fix** – give each conformer a distinct name so that file I/O
+                # does not overwrite previous results.
+                # The name is the parent complex name plus a zero‑padded index.
+                # -----------------------------------------------------------------
+                mol.name = f\"{self.name}_conf{conf_counter:03d}\"
+                conf_counter += 1
+
+                conformers.append(mol)
+
+        return conformers
```

### What the patch does
1. Introduces a `conf_counter` that increments for every generated conformer.
2. Sets the molecule name to `"{parent_name}_conf{index:03d}"` (e.g. `water_trimer_conf000`,
   `water_trimer_conf001`, …).  
   This guarantees a unique stem for every XTB input/output file.
3. Leaves the rest of the generation logic untouched.

If the project stores the conformer list elsewhere (e.g. `self.conformers`), the unique name
propagates automatically to the optimisation routine because it uses the molecule’s `name`
attribute when constructing fil

=== TASK snowflakedb__snowflake-connector-python-1856 | repo snowflakedb/snowflake-connector-python | python
--- Issue
SNOW-1013234: Fix config permissions warning message 
### What is the current behavior?

If permissions / owner of config file is incorrect, the command outputs long one-liner containing two commands (`chmod` and `chown`) to call to fix the issue.
1. Many users tend to overlook that there are two commands and give up after one does not fix the issue
2. In Snowflake CLI on MacOS default config path is inside "Application Support" directory - space inside the path turned out to be problematic for some users

```
/Users/pczajka/Snowflake/venv3.12/lib/python3.12/site-packages/snowflake/connector/config_manager.py:344: UserWarning: Bad owner or permissions on /Users/pczajka/Library/Application Support/snowflake/config.toml. To change owner, run `chown $USER /Users/pczajka/Library/Application Support/snowflake/config.toml`. To restrict permissions, run `chmod 0600 /Users/pczajka/Library/Application Support/snowflake/config.toml`.
  warn(f"Bad owner or permissions on {str(filep)}{chmod_message}")
  ```

### What is the desired behavior?

Reformattinmg the message to highlight that there are two commands to call (fix 1) and add quotes around filenames (fix 2).
For example:
```
/Users/pczajka/Snowflake/venv3.12/lib/python3.12/site-packages/snowflake/connector/config_manager.py:344: UserWarning: Bad owner or permissions on /Users/pczajka/Library/Application Support/snowflake/config.toml.
 * To change owner, run `chown $USER "/Users/pczajka/Library/Application Support/snowfla
--- REAL FIX (ground truth)
diff --git a/DESCRIPTION.md b/DESCRIPTION.md
index a13a3feb..1aa2982a 100644
--- a/DESCRIPTION.md
+++ b/DESCRIPTION.md
@@ -18,6 +18,7 @@ Source code is also available at: https://github.com/snowflakedb/snowflake-conne
   - Added support for connecting using an existing connection via the session and master token.
   - Added support for connecting to Snowflake by authenticating with multiple SAML IDP using external browser.
   - Fixed compilation issue due to missing cstdint header on gcc13.
+  - Improved config permissions warning message.
 
 - v3.6.0(December 09,2023)
 
diff --git a/src/snowflake/connector/config_manager.py b/src/snowflake/connector/config_manager.py
index d9ecf80a..e981a40c 100644
--- a/src/snowflake/connector/config_manager.py
+++ b/src/snowflake/connector/config_manager.py
@@ -336,7 +336,7 @@ class ConfigManager:
             ):
                 # for non-Windows, suggest change to 0600 permissions.
                 chmod_message = (
-                    f". To change owner, run `chown $USER {str(filep)}`. To restrict permissions, run `chmod 0600 {str(filep)}`."
+                    f'.\n * To change owner, run `chown $USER "{str(filep)}"`.\n * To restrict permissions, run `chmod 0600 "{str(filep)}"`.\n'
                     if not IS_WINDOWS
                     else ""
                 )

--- Proposal A
## Root cause
`config_manager.py` builds the warning message as a single line string that concatenates the two fix commands.  
Because the filename is inserted without quoting, paths that contain spaces (e.g. *~/Library/Application Support/*) break the displayed command.  
The single‑line format also makes it easy for users to miss that **both** commands are required.

## Fix
* Build the warning message as a multi‑line string.
* Quote the filename (`"{filep}"`) in both commands so that spaces are handled correctly.
* Add a leading bullet (`*`) on each line to make the two commands visually distinct.

```diff
--- a/snowflake/connector/config_manager.py
+++ b/snowflake/connector/config_manager.py
@@
-    # Build warning about bad owner / permissions.
-    chmod_message = ""
-    if not filep.owner() == getpass.getuser():
-        chmod_message += (
-            f". To change owner, run `chown $USER {filep}`."
-        )
-    if not (filep.stat().st_mode & 0o600) == 0o600:
-        chmod_message += (
-            f" To restrict permissions, run `chmod 0600 {filep}`."
-        )
-
-    warn(f"Bad owner or permissions on {str(filep)}{chmod_message}")
+    # Build warning about bad owner / permissions.
+    # NOTE: The warning is now multi‑line and the filename is quoted so that
+    # paths containing spaces are handled correctly.
+    warning_lines = [
+        f'Bad owner or permissions on "{filep}".',
+    ]
+
+    # Owner fix
+    if not filep.owner() == getpass.getuser():
+        warning_lines.append(
+            f' * To change owner, run `chown $USER "{filep}"`.'
+        )
+
+    # Permissions fix
+    if not (filep.stat().st_mode & 0o600) == 0o600:
+        warning_lines.append(
+            f' * To restrict permissions, run `chmod 0600 "{filep}"`.'
+        )
+
+    # Join the lines with a newline so the warning appears as a readable block.
+    warn("\n".join(warning_lines))
```

### Explanation of changes
1. **Quote the path** – `"{filep}"` ensures that spaces or other special characters are interpreted correctly by the shell commands shown to the user.
2. **Multi‑line formatting** – Using a list of strings (`warning_lines`) that are later joined with `"\n"` produces a clear, readable block.
3. **Bulleted list** – Prefixing each fix line with `*` makes it obvious that there are two separate actions.
4. The logic that decides whether each line should be added remains unchanged; only the presentation is altered.

## How to verify
1. **Unit‑test / integration test**  
   *Create a temporary config file with a space in its path* (e.g. `tmpdir / "My Config/config.toml"`).  
   *Set the file owner to a different user* (or mock `filep.owner()` to return a wrong value) and set its mode to something other than `0600`.  
   *Import the module and trigger the warning* (e.g. call the function that validates the config).  
   Capture `stderr` (or use `warnings.catch_warnings`) and assert that:
   * The warning contains **two separate lines** after the first line.
   * Each command line is **quoted** (`"…/My Config/config.toml"`).
   * The lines start with `*` (bullet) as shown in the desired output.

2. **Manual verification**  
   *Run the connector on a machine where the config file lives under `~/Library/Application Support/snowflake/config.toml` with deliberately wrong permissions.*  
   Observe that the warning printed to the console matches the new format:
   ```
   UserWarning: Bad owner or permissions on "/Users/.../Library/Applicati
--- Proposal B
## Root cause
`config_manager.py` builds the warning message as a single line string that appends the two fix commands directly after the file path.  
* No line‑breaks or bullet points are added, so users see a long one‑liner and may miss that **both** commands are required.  
* The file path is inserted without quoting, so paths containing spaces (e.g. “Application Support”) break when copied‑pasted into a shell.

## Fix
```diff
--- a/snowflake/connector/config_manager.py
+++ b/snowflake/connector/config_manager.py
@@
-    if not _has_valid_owner_and_perms(filep):
-        chmod_message = (
-            f". To change owner, run `chown $USER {filep}`."
-            f" To restrict permissions, run `chmod 0600 {filep}`."
-        )
-        warn(f"Bad owner or permissions on {str(filep)}{chmod_message}")
+    if not _has_valid_owner_and_perms(filep):
+        # NOTE:
+        #   * The warning is shown to end‑users, therefore it must be easy to read
+        #     and copy‑paste.  We add line breaks and bullet points to make the two
+        #     required commands obvious.
+        #   * Paths may contain spaces (e.g. “Application Support”).  Wrapping the
+        #     path in double quotes prevents the shell from mis‑interpreting the
+        #     argument.
+        quoted_path = f'"{filep}"'
+        chmod_message = (
+            f".\n"
+            f" * To change owner, run `chown $USER {quoted_path}`.\n"
+            f" * To restrict permissions, run `chmod 0600 {quoted_path}`."
+        )
+        warn(f"Bad owner or permissions on {str(filep)}{chmod_message}")
*** End of File ***
```

### What the patch does
1. **Quotes the filename** (`quoted_path = f'"{filep}"'`) so any spaces are safely handled.
2. **Reformats the message**:
   * Starts a new line after the file path (`".\n"`).
   * Adds a bullet (`*`) before each command.
   * Ends each command line with a period for consistency.
3. Keeps the original warning call (`warn(...)`) unchanged apart from the new, clearer message.

## How to verify
1. **Unit‑test / integration test**  
   *Create a temporary config file with deliberately bad permissions* (e.g. `chmod 0644`).  
   *Invoke the code path that checks the config (e.g. `ConfigManager().load()` or the public helper that triggers the warning).*  
   Capture `stderr` (or the logged warning) and assert that:
   * The warning contains **two separate lines** after the file path.
   * Each line starts with `*` and contains the appropriate command.
   * The file path inside the back‑ticks is wrapped in double quotes.
   * Example expected snippet:
   ```
   UserWarning: Bad owner or permissions on /tmp/tmp dir/config.toml.
    * To change owner, run `chown $USER "/tmp/tmp dir/config.toml"`.
    * To restrict permissions, run `chmod 0600 "/tmp/tmp dir/config.toml"`.
   ```

2. **Manual verification**  
   Run the connector on a machine where the config resides under a directory with spaces (e.g. the default macOS location).  
   Observe the printed warning; it should now be multi‑line, bullet‑pointed, and the path quoted, matching the example in the issue description.

3. **Cross‑platform check**  
   Ensure the warning still appears correctly on Linux/macOS/Windows (where quoting is harmless) and that no regression is introduced for the existing permission‑checking logic.

=== TASK PyCQA__flake8-bugbear-287 | repo PyCQA/flake8-bugbear | python
--- Issue
Proposed check: don't allow argument unpacking in a call after a keyword argument
Imagine a function like

```py
def foo(bar, baz):
    pass
```

The following call is a syntactically incorrect:

```py
foo(baz="baz", "bar")
```

However, with unpacking the following is allowed:

```py
foo(baz="baz", *["bar"])
```

[However](https://github.com/python/cpython/issues/82741#issuecomment-1093845041),

> [T]he resulting argument binding is awkward, and almost never does what you want/expect it to

It was [not ruled a bug or a behavior that can or should be deprecated](https://github.com/python/cpython/issues/82741#issuecomment-1093845049). Still, I think in most cases this is probably an oversight and should not be used.

If we want this check, I would be willing to contribute a patch.
--- REAL FIX (ground truth)
diff --git a/README.rst b/README.rst
index d7981f7..b7d4f38 100644
--- a/README.rst
+++ b/README.rst
@@ -160,6 +160,13 @@ the loop, because `late-binding closures are a classic gotcha
 This check identifies exception types that are specified in multiple ``except``
 clauses. The first specification is the only one ever considered, so all others can be removed.
 
+**B026**: Star-arg unpacking after a keyword argument is strongly discouraged, because
+it only works when the keyword parameter is declared after all parameters supplied by
+the unpacked sequence, and this change of ordering can surprise and mislead readers.
+There was `cpython discussion of disallowing this syntax
+<https://github.com/python/cpython/issues/82741>`_, but legacy usage and parser
+limitations make it difficult.
+
 Opinionated warnings
 ~~~~~~~~~~~~~~~~~~~~
 
diff --git a/bugbear.py b/bugbear.py
index 4b742b7..cc92b92 100644
--- a/bugbear.py
+++ b/bugbear.py
@@ -354,6 +354,8 @@ class BugBearVisitor(ast.NodeVisitor):
                 ):
                     self.errors.append(B010(node.lineno, node.col_offset))
 
+            self.check_for_b026(node)
+
         self.generic_visit(node)
 
     def visit_Assign(self, node):
@@ -641,6 +643,22 @@ class BugBearVisitor(ast.NodeVisitor):
 
         self.errors.append(B024(node.lineno, node.col_offset, vars=(node.name,)))
 
+    def check_for_b026(self, call: ast.Call):
+        if not call.keywords:
+            return
+
+        starreds = [arg for arg in call.args if isinstance(arg, ast.Starred)]
+        if not starreds:
+            return
+
+        first_keyword = call.keywords[0].value
+        for starred in starreds:
+            if (starred.lineno, starred.col_offset) > (
+                first_keyword.lineno,
+                first_keyword.col_offset,
+            ):
+                self.errors.append(B026(starred.lineno, starred.col_offset))
+
     def _get_assigned_names(self, loop_node):
         loop_targets = (ast.For, ast.AsyncFor, ast.comprehension)
         for node in children_in_scope(loop_node):
@@ -1203,6 +1221,14 @@ B025 = Error(
         " will be considered and all other except catches can be safely removed."
     )
 )
+B026 = Error(
+    message=(
+        "B026 Star-arg unpacking after a keyword argument is strongly discouraged, "
+        "because it only works when the keyword parameter is declared after all "
+        "parameters supplied by the unpacked sequence, and this change of ordering can "
+        "surprise and mislead readers."
+    )
+)
 
 # Warnings disabled by default.
 B901 = Error(

--- Proposal A
## Root cause
`flake8-bugbear` does not currently check the ordering of arguments in a function call.  
Python’s grammar permits a starred argument (`*iterable`) **after** a keyword argument, e.g.:

```python
foo(baz="baz", *["bar"])
```

While syntactically valid, this pattern is almost always a mistake and the CPython issue tracker notes that the resulting binding is “awkward”.  
Because the AST separates positional arguments (`call.args`) from keyword arguments (`call.keywords`), the existing checks never see the interleaving order and therefore never flag this situation.

## Fix
Add a new check **B018** that walks every `ast.Call` node, looks for a `Starred` argument that appears **after** any keyword argument, and reports a warning.

```diff
--- a/flake8_bugbear/checks.py
+++ b/flake8_bugbear/checks.py
@@
 from . import utils
 from .base import BaseCheck, Error
 
@@
 __all__ = [
@@
     "B017",
     "B018",   # ← new check added to the public list
 ]
 
@@
 class B017(BaseCheck):
@@
     def visit_With(self, node: ast.With) -> None:
         # existing implementation …
         self.generic_visit(node)
 
+# ---------------------------------------------------------------------------
+# B018: argument unpacking after a keyword argument
+# ---------------------------------------------------------------------------
+
+class B018(BaseCheck):
+    """
+    Detect calls where a ``*args`` (``ast.Starred``) appears after a keyword
+    argument.  The pattern ``func(key=val, *iterable)`` is legal Python but
+    almost never does what the author intends.
+    """
+
+    #: error code and message
+    code = "B018"
+    message = "B018 argument unpacking after keyword argument"
+
+    def visit_Call(self, node: ast.Call) -> None:
+        """
+        Walk a Call node and emit an error for each ``Starred`` argument that
+        is positioned after any keyword argument.
+        """
+        # No keywords → nothing to check
+        if not node.keywords:
+            self.generic_visit(node)
+            return
+
+        # Gather the source position of each keyword argument (ignore **kwargs)
+        kw_positions = [
+            (kw.lineno, kw.col_offset)
+            for kw in node.keywords
+            if kw.arg is not None  # regular keyword, not **kwargs
+        ]
+
+        # If there are no regular keywords (only **kwargs) we also skip – the
+        # ordering rule does not apply to ``**``.
+        if not kw_positions:
+            self.generic_visit(node)
+            return
+
+        # Determine the earliest keyword position (the first keyword in source)
+        first_kw_line, first_kw_col = min(kw_positions)
+
+        for arg in node.args:
+            if isinstance(arg, ast.Starred):
+                # ``Starred`` nodes have ``lineno``/``col_offset`` like any other
+                # expression.  If the starred argument starts *after* the first
+                # keyword, we flag it.
+                if (arg.lineno, arg.col_offset) > (first_kw_line, first_kw_col):
+                    self.error_from_node(arg, self.message)
+        # Continue traversing the tree
+        self.generic_visit(node)
+
*** End of File ***
--- a/flake8_bugbear/__init__.py
+++ b/flake8_bugbear/__init__.py
@@
 from .checks import (
@@
     B017,
+    B018,
 )
 
 __all__ = [
@@
     "B017",
+    "B018",
 ]
 
*** End of File ***
```

### Explanation of the diff
1. **`__all__` updates** – expose the new check (`B018`) from the package.
2. **Import list
--- Proposal B
## Root cause
`flake8-bugbear` does not currently check for the pattern where a starred argument (`*args`) appears **after** a keyword argument in a function call.  
Python’s grammar allows this (`foo(baz="baz", *["bar"])`) but the resulting argument binding is confusing and almost never what the programmer intends. Because the AST separates positional (`node.args`) and keyword (`node.keywords`) arguments, the existing checks never see the relative ordering and therefore never flag this situation.

## Fix
Add a new check **B904** that walks `ast.Call` nodes, finds any normal keyword argument (`kw.arg is not None`) and then looks for a `ast.Starred` argument whose source position comes *after* that keyword. When such a pattern is found, report a `B904` warning.

```diff
--- a/flake8_bugbear/checks.py
+++ b/flake8_bugbear/checks.py
@@
 from __future__ import annotations
@@
 import ast
 import sys
@@
 from . import utils
@@
 __all__ = [
@@
     "B902",
     "B903",
+    "B904",          # new check
 ]
@@
 class B902(Check):
@@
     pass
@@
 class B903(Check):
@@
     pass
+
+# ----------------------------------------------------------------------
+# B904: argument unpacking after a keyword argument
+# ----------------------------------------------------------------------
+
+class B904(Check):
+    """
+    Detect calls where a starred argument (``*args``) appears after a
+    keyword argument.  This construct is syntactically valid but
+    produces confusing argument binding and is almost never what the
+    author intended.
+
+    Example::
+
+        foo(baz=\"baz\", *[\"bar\"])   # <-- flagged
+    """
+
+    #: The error code and message used by flake8.
+    #: The message follows the style of other bugbear checks.
+    #: ``B904`` is the next free code in the series.
+    name = "bugbear"
+    version = "2.1.0"
+    #: This check is enabled by default.
+    off_by_default = False
+
+    def visit_Call(self, node: ast.Call) -> None:
+        """
+        Visit a ``Call`` node and emit a warning if a ``Starred`` argument
+        appears after a normal keyword argument.
+        """
+        # Gather normal keyword arguments (kw.arg is not None)
+        keyword_positions = [
+            (kw.lineno, kw.col_offset)
+            for kw in node.keywords
+            if kw.arg is not None
+        ]
+        if not keyword_positions:
+            # No keyword arguments → nothing to check.
+            return
+
+        # Determine the earliest position of any keyword argument.
+        # We need the *latest* keyword position that precedes a starred arg,
+        # but the simplest safe test is: if a starred arg's position is
+        # greater than *any* keyword position, we flag it.
+        for arg in node.args:
+            if isinstance(arg, ast.Starred):
+                # ``arg`` may be on a different line than the keyword.
+                # Compare line first, then column for the same line.
+                for kw_line, kw_col in keyword_positions:
+                    if (arg.lineno > kw_line) or (
+                        arg.lineno == kw_line and arg.col_offset > kw_col
+                    ):
+                        self.error_from_node(
+                            arg,
+                            "B904 argument unpacking after keyword argument is confusing",
+                        )
+                        # One warning per starred argument is enough.
+                        break
+
+        # Continue generic traversa

=== TASK pajlads__dinkplugin-273 | repo pajlads/DinkPlugin | java
--- Issue
200M experience notification
Currently, our post-99 notifications are enabled with the "Notify on Virtual Levels" option - I feel like this option should also notify when the user reaches 200M experience

There are some things that would need to be tested in-game for this feature to make sure it doesn't continue to notify

1. When a user has 199,999,999 XP and receives 2 XP
2. When a user has 200,000,000 XP - what do further skill change events look like?
--- REAL FIX (ground truth)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 40fc47e..276ca09 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,5 +1,6 @@
 ## Unreleased
 
+- Minor: Fire level notification upon reaching max skill experience of 200M. (#273)
 - Minor: Include clan name in notificiation metadata. (#274)
 - Dev: Update gradle wrapper to v8.2.1 patch version. (#276)
 
diff --git a/README.md b/README.md
index bd86115..1a10461 100644
--- a/README.md
+++ b/README.md
@@ -270,6 +270,8 @@ Lastly, Dink makes exceptions for Inferno and TzHaar Fight Cave; deaths in these
 }
 ```
 
+Note: Level 127 in JSON corresponds to attaining max experience in a skill (200M).
+
 </details>
 
 ### Loot:
diff --git a/src/main/java/dinkplugin/notifiers/LevelNotifier.java b/src/main/java/dinkplugin/notifiers/LevelNotifier.java
index 1db40a1..7a94db5 100644
--- a/src/main/java/dinkplugin/notifiers/LevelNotifier.java
+++ b/src/main/java/dinkplugin/notifiers/LevelNotifier.java
@@ -32,6 +32,7 @@ import static net.runelite.api.Experience.MAX_REAL_LEVEL;
 @Slf4j
 @Singleton
 public class LevelNotifier extends BaseNotifier {
+    public static final int LEVEL_FOR_MAX_XP = Experience.MAX_VIRT_LEVEL + 1; // 127
     private static final int INIT_CLIENT_TICKS = 50; // 1000ms
     private static final String COMBAT_NAME = "Combat";
     private static final Set<String> COMBAT_COMPONENTS;
@@ -62,8 +63,7 @@ public class LevelNotifier extends BaseNotifier {
             for (Skill skill : Skill.values()) {
                 int level = client.getRealSkillLevel(skill); // O(1)
                 if (level >= MAX_REAL_LEVEL) {
-                    // uses log(n) operation to support virtual levels
-                    level = Experience.getLevelForXp(client.getSkillExperience(skill));
+                    level = getLevel(client.getSkillExperience(skill));
                 }
                 currentLevels.put(skill.getName(), level);
             }
@@ -112,7 +112,7 @@ public class LevelNotifier extends BaseNotifier {
     private void handleLevelUp(String skill, int level, int xp) {
         if (!isEnabled()) return;
 
-        int virtualLevel = level < MAX_REAL_LEVEL ? level : Experience.getLevelForXp(xp); // avoid log(n) query when not needed
+        int virtualLevel = level < MAX_REAL_LEVEL ? level : getLevel(xp); // avoid log(n) query when not needed
         Integer previousLevel = currentLevels.put(skill, virtualLevel);
 
         if (previousLevel == null) {
@@ -192,7 +192,7 @@ public class LevelNotifier extends BaseNotifier {
             Integer level = currentLevels.get(skill);
             skillMessage
                 .component(Replacements.ofWiki(skill, COMBAT_NAME.equals(skill) ? "Combat level" : skill))
-                .component(Replacements.ofText(" to " + level));
+                .component(Replacements.ofText(" to " + (level < LEVEL_FOR_MAX_XP ? level : "Max XP (200M)")));
             lSkills.put(skill, level);
         }
 
@@ -248,7 +248,7 @@ public class LevelNotifier extends BaseN
--- Proposal A
## Root cause
The plugin only sends a “virtual level” notification when the **skill level** changes.  
When a player reaches the 200 000 000 XP cap the level does **not** increase any further, so the existing “Notify on Virtual Levels” check never fires. Consequently the player is never notified that they have hit the 200 M XP milestone.

## Fix
Add an explicit XP‑threshold check for the 200 M mark in the experience‑change handler. The notification should be sent only once when the player’s XP crosses from below 200 M to ≥ 200 M and only when the “Notify on Virtual Levels” option is enabled.

```diff
--- a/src/main/java/com/pajlads/dinkplugin/notifications/ExperienceNotifier.java
+++ b/src/main/java/com/pajlads/dinkplugin/notifications/ExperienceNotifier.java
@@
     public void onSkillExperienceChanged(Skill skill, int oldXp, int newXp)
     {
         // existing virtual‑level notification logic
         if (config.notifyOnVirtualLevels())
         {
-            int oldLevel = ExperienceInfo.getLevel(oldXp);
-            int newLevel = ExperienceInfo.getLevel(newXp);
-            if (oldLevel != newLevel)
-            {
-                sendVirtualLevelNotification(skill, newLevel);
-            }
+            int oldLevel = ExperienceInfo.getLevel(oldXp);
+            int newLevel = ExperienceInfo.getLevel(newXp);
+
+            // Notify on normal virtual‑level ups
+            if (oldLevel != newLevel)
+            {
+                sendVirtualLevelNotification(skill, newLevel);
+            }
+
+            // ---- NEW: 200 M XP milestone notification ----
+            // The level does not increase after the 200 M cap, so we need a
+            // separate check that fires exactly once when the XP crosses the
+            // threshold.
+            final int XP_200M = 200_000_000;
+            boolean crossed200M = oldXp < XP_200M && newXp >= XP_200M;
+            if (crossed200M)
+            {
+                // Use the same notification style as virtual‑level ups but
+                // with a distinct message.
+                sendVirtualLevelNotification(skill, ExperienceInfo.getLevel(newXp),
+                        "200M XP reached!");
+            }
         }
     }
 
@@
-    private void sendVirtualLevelNotification(Skill skill, int level)
+    /** Sends a virtual‑level notification. If a custom message is supplied it
+     *  replaces the default “Virtual level X reached” text. */
+    private void sendVirtualLevelNotification(Skill skill, int level)
+    {
+        sendVirtualLevelNotification(skill, level, null);
+    }
+
+    private void sendVirtualLevelNotification(Skill skill, int level, @Nullable String customMessage)
     {
         String skillName = skill.getName();
-        String message = String.format("%s reached virtual level %d!", skillName, level);
+        String message = customMessage != null
+                ? String.format("%s: %s", skillName, customMessage)
+                : String.format("%s reached virtual level %d!", skillName, level);
 
         notifier.notify(message);
     }
```

### What was changed
1. **Added a 200 M XP crossing check** inside the existing `notifyOnVirtualLevels` block.
2. **Created an overloaded `sendVirtualLevelNotification`** that can accept a custom message, allowing us to reuse the existing notification styling while providing a distinct “200M XP reached!” text.
3. **Ensured the notification fires only once** by checking the transition from `< 200 M` to `≥ 200 M
--- Proposal B
## Root cause
`LevelNotifier` only generates **XP‑milestone** notifications when the skill’s experience crosses a configured interval (e.g., every 1 M XP).  
The check is performed **before** the “virtual level” handling, and it stops when the next interval would be larger than the skill’s current XP.  
Because the maximum skill experience (200 000 000) is not guaranteed to be an exact multiple of the interval, the notifier never fires a notification when a player reaches the 200 M cap.  
Consequently, users who have “Notify on Virtual Levels” enabled never receive a notification for the final 200 M milestone.

## Fix
* Add explicit handling for the **maximum‑experience** milestone (200 M) in `LevelNotifier`.
* Ensure the notification is emitted **once** even if the interval does not divide the max value.
* Keep the logic independent of the “Notify on Virtual Levels” toggle.
* Track the last notified XP per skill to avoid duplicate notifications.

```diff
--- a/src/main/java/com/pajlads/dinkplugin/notifier/LevelNotifier.java
+++ b/src/main/java/com/pajlads/dinkplugin/notifier/LevelNotifier.java
@@
 import com.pajlads.dinkplugin.notification.NotificationType;
 import com.pajlads.dinkplugin.notification.XpNotificationData;
 import net.runelite.api.Skill;
 import net.runelite.api.events.StatChanged;
 import java.util.EnumMap;
 import java.util.Map;
+import static com.pajlads.dinkplugin.util.Constants.MAX_XP; // new constant

 public class LevelNotifier
 {
     private static final int DEFAULT_MILESTONE_INTERVAL = 1_000_000; // 1M XP
@@
-    private final Map<Skill, Integer> lastNotifiedXp = new EnumMap<>(Skill.class);
+    /** Tracks the last XP value for which a milestone notification was sent. */
+    private final Map<Skill, Integer> lastNotifiedXp = new EnumMap<>(Skill.class);
@@
-    @Subscribe
-    public void onStatChanged(StatChanged event)
-    {
-        Skill skill = event.getSkill();
-        int oldXp = event.getPreviousValue();
-        int newXp = event.getCurrentValue();
-
-        // Virtual level notifications are handled elsewhere; we only care about raw XP milestones.
-        if (config.notifyVirtualLevels() && skill.getLevel() >= 99)
-        {
-            // No XP‑milestone notification needed when virtual levels are enabled.
-            return;
-        }
-
-        int interval = config.xpMilestoneInterval() > 0 ? config.xpMilestoneInterval() : DEFAULT_MILESTONE_INTERVAL;
-
-        // Determine the next milestone threshold after the last notified XP.
-        int lastNotified = lastNotifiedXp.getOrDefault(skill, 0);
-        int nextMilestone = ((lastNotified / interval) + 1) * interval;
-
-        // If the new XP crossed the next milestone, fire a notification.
-        if (newXp >= nextMilestone && oldXp < nextMilestone)
-        {
-            XpNotificationData data = new XpNotificationData(skill, nextMilestone, interval);
-            notifier.notify(NotificationType.XP_MILESTONE, data);
-            lastNotifiedXp.put(skill, nextMilestone);
-        }
-    }
+    @Subscribe
+    public void onStatChanged(StatChanged event)
+    {
+        Skill skill = event.getSkill();
+        int oldXp = event.getPreviousValue();
+        int newXp = event.getCurrentValue();
+
+        // -----------------------------------------------------------------
+        // 1️⃣  Virtual‑level handling (unchanged from previous behaviour)
+        // -----------------------------------------------------------------
+        // Th

=== TASK klothoplatform__klotho-354 | repo klothoplatform/klotho | go
--- Issue
Kubernetes/Helm plugin does not import bucket name as environment variable
with our change to make s3 bucket names and go secrets environment variables, we need to make the consistent change in the kubernetes plugin

```
(base) jordansinger@Jordans-MacBook-Pro test_pulumi_destroy % kubectl logs executor-844bc54bd8-f74qd -p
Listening on :3000
2023/03/13 22:36:47 s3blob.OpenBucket: bucketName is required
```

We should also warn if an env var is in the exec unit that the plugin doesnt recognize so that we can catch this
--- REAL FIX (ground truth)
diff --git a/.github/workflows/govulncheck.yaml b/.github/workflows/govulncheck.yaml
index 9892ae7f..c985e076 100644
--- a/.github/workflows/govulncheck.yaml
+++ b/.github/workflows/govulncheck.yaml
@@ -13,7 +13,7 @@ jobs:
     steps:
       - uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
       - uses: actions/cache@v2
         with:
           path: |
diff --git a/.github/workflows/lint.yaml b/.github/workflows/lint.yaml
index 673d3a10..b587004c 100644
--- a/.github/workflows/lint.yaml
+++ b/.github/workflows/lint.yaml
@@ -13,7 +13,7 @@ jobs:
     steps:
       - uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
       - uses: actions/checkout@v3
       - uses: actions/cache@v2
         with:
diff --git a/.github/workflows/prettier.yaml b/.github/workflows/prettier.yaml
index db3523df..44b8aaa5 100644
--- a/.github/workflows/prettier.yaml
+++ b/.github/workflows/prettier.yaml
@@ -13,7 +13,7 @@ jobs:
     steps:
       - uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
       - uses: actions/checkout@v3
       - name: List files to check
         run: |
diff --git a/.github/workflows/release.yaml b/.github/workflows/release.yaml
index e3428980..c0b82331 100644
--- a/.github/workflows/release.yaml
+++ b/.github/workflows/release.yaml
@@ -25,7 +25,7 @@ jobs:
       - name: Setup Go
         uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
 
       - name: Setup Zig
         uses: goto-bus-stop/setup-zig@v1
diff --git a/.github/workflows/staticcheck.yaml b/.github/workflows/staticcheck.yaml
index 6bad3713..7a323f84 100644
--- a/.github/workflows/staticcheck.yaml
+++ b/.github/workflows/staticcheck.yaml
@@ -13,7 +13,7 @@ jobs:
     steps:
       - uses: actions/setup-go@v3
         with:
-          go-version: '1.19.6'
+          go-version: '1.20.2'
       - uses: actions/checkout@v3
       - uses: actions/cache@v2
         with:
diff --git a/pkg/analytics/tracking_utils.go b/pkg/analytics/tracking_utils.go
index 712855ff..20d9d777 100644
--- a/pkg/analytics/tracking_utils.go
+++ b/pkg/analytics/tracking_utils.go
@@ -5,11 +5,11 @@ import (
 	"bytes"
 	"encoding/json"
 	"fmt"
-	"go.uber.org/zap"
-	"math"
 	"net/http"
 	"time"
 
+	"go.uber.org/zap"
+
 	"github.com/klothoplatform/klotho/pkg/core"
 )
 
@@ -53,11 +53,6 @@ func CompressFiles(input *core.InputFiles) ([]byte, error) {
 			UncompressedSize64: uint64(buf.Len()),
 			Modified:           now,
 		}
-		if header.UncompressedSize64 >= math.MaxUint32 {
-			header.UncompressedSize = math.MaxUint32
-		} else {
-			header.UncompressedSize = uint32(header.UncompressedSize64)
-		}
 
 		headerWriter, err := zipWriter.CreateHeader(header)
 		if err != nil {
diff --git a/pkg/core/topology.go b/pkg/core/topology.go
index e9a3bc11..e8e7287c 100644
--- a/pkg/core/topology.go
+++ b/pkg/c
--- Proposal A
## Root cause
The Kubernetes/Helm plugin still treats S3 bucket names (and Go secrets) as **static configuration values** that are rendered into the Helm chart.  
After the core change that makes bucket names and Go secrets available **only through environment variables**, the plugin no longer injects those variables into the generated container spec. Consequently the runtime code that calls `s3blob.OpenBucket` cannot find the bucket name and aborts with:

```
s3blob.OpenBucket: bucketName is required
```

In addition, the plugin never warns when an execution unit contains an environment variable that the plugin does not know how to provide, making such mismatches hard to detect.

## Fix
* Update the Helm plugin to **export bucket names and Go secrets as environment variables** on the generated containers.
* Introduce a helper that maps a resource (S3 bucket, Go secret) to the env‑var name expected by the runtime.
* After the env‑var list for an exec unit is built, compare it with the set of variables the plugin can provide and emit a warning for any unknown ones.

```diff
--- a/plugins/kubernetes/helm/execunit.go
+++ b/plugins/kubernetes/helm/execunit.go
@@
 import (
     "fmt"
     "log"
     "strings"
 
+    "github.com/klothoplatform/klotho/pkg/ir"
+    "github.com/klothoplatform/klotho/pkg/k8s"
 )
 
 // ExecUnit represents a container that will be run in a pod.
 type ExecUnit struct {
@@
 func (eu *ExecUnit) addEnvFromResources(resources []ir.Resource) {
-    // TODO: old implementation only added static config values.
-    // The new implementation must also add environment variables for
-    // resources that are now exposed via env vars (e.g. S3 buckets, Go secrets).
+    // Existing behaviour: add static config values (e.g. ports, flags).
+    // New behaviour: also add environment variables for resources that are
+    // exported as env‑vars by the core runtime (S3 buckets, Go secrets, etc.).
+
+    // Keep track of which env vars we have supplied so we can warn about unknown ones later.
+    providedEnv := map[string]struct{}{}
+
+    for _, r := range resources {
+        switch res := r.(type) {
+        case *ir.S3Bucket:
+            // The runtime expects the bucket name in an env var named
+            // KLOTHO_S3_BUCKET_<resource-id> (lower‑cased id, dashes replaced with underscores).
+            envName := fmt.Sprintf("KLOTHO_S3_BUCKET_%s", sanitizeEnvName(res.ID))
+            eu.Env = append(eu.Env, k8s.EnvVar{
+                Name:  envName,
+                Value: res.Name, // bucket name is known at compile time
+            })
+            providedEnv[envName] = struct{}{}
+
+        case *ir.GoSecret:
+            // Go secrets are also exported via env vars.
+            // The convention is KLOTHO_GO_SECRET_<secret-id>
+            envName := fmt.Sprintf("KLOTHO_GO_SECRET_%s", sanitizeEnvName(res.ID))
+            eu.Env = append(eu.Env, k8s.EnvVar{
+                Name:  envName,
+                Value: res.Value, // secret value is known at compile time (or will be replaced by a secret reference)
+            })
+            providedEnv[envName] = struct{}{}
+
+        // Add other resource types here as needed.
+        default:
+            // No env var needed for this resource type.
+        }
+    }
+
+    // After we have added all known env vars, warn about any env vars that the exec unit
+    // expects but the plugin does not recognise.
+    eu.warnUnknownEnvVars(providedEnv)
 }
 
+// sanitizeE
--- Proposal B
## Root cause
The Kubernetes/Helm plugin only propagates a fixed set of environment variables from an execution unit into the generated Helm chart.  
When the core engine was changed to expose **S3 bucket names** (and Go secrets) as environment variables, the Helm plugin was not updated, so the bucket‑name variable never made it into the Helm values file.  
Consequently the container that runs `s3blob.OpenBucket` receives an empty `bucketName` and crashes with:

```
s3blob.OpenBucket: bucketName is required
```

In addition, the plugin silently drops any environment variable it does not recognise, making it hard to notice missing mappings.

## Fix
* Extend the Helm plugin to copy **all** environment variables defined on the execution unit into the Helm values, not just a hard‑coded whitelist.
* Keep the existing whitelist for variables that need special handling (e.g., `KLOTHO_LOG_LEVEL`) and emit a **warning** for any variable that the plugin does not have explicit support for.
* The warning is logged through the plugin’s logger so that developers can see which variables are being ignored.

```diff
--- a/plugins/kubernetes/helm/helm_plugin.go
+++ b/plugins/kubernetes/helm/helm_plugin.go
@@
 import (
     "fmt"
     "strings"
 
     "github.com/klothoplatform/klotho/pkg/engine"
+    "github.com/klothoplatform/klotho/pkg/log"
     "github.com/klothoplatform/klotho/pkg/k8s"
     "github.com/klothoplatform/klotho/pkg/k8s/helm"
 )
 
 // HelmPlugin implements the engine.Plugin interface for generating Helm charts.
 type HelmPlugin struct{}
@@
 func (p *HelmPlugin) Generate(ctx *engine.Context, unit *engine.ExecutionUnit) (*helm.Chart, error) {
@@
-    // Populate environment variables that are known to the Helm plugin.
-    // NOTE: this list must be kept in sync with the runtime that consumes the chart.
-    knownEnv := map[string]bool{
-        "KLOTHO_LOG_LEVEL": true,
-        // other known vars …
-    }
-
-    for _, env := range unit.Environment {
-        if knownEnv[env.Name] {
-            // Directly copy known env vars into the values file.
-            values[env.Name] = env.Value
-        } else {
-            // Silently ignore unknown env vars (old behaviour).
-        }
-    }
+    // -------------------------------------------------------------------------
+    // Environment variable handling
+    // -------------------------------------------------------------------------
+    // Historically the Helm plugin only copied a hard‑coded whitelist of env
+    // vars into the generated values file.  This caused newly‑added variables
+    // (e.g. the S3 bucket name) to be dropped, leading to runtime errors such as
+    // “s3blob.OpenBucket: bucketName is required”.
+    //
+    // The new behaviour is:
+    //   * Copy **all** env vars defined on the execution unit into the Helm
+    //     values file so that the container can read them at runtime.
+    //   * Keep a whitelist of variables that require special handling (e.g.
+    //     boolean conversion, secret masking, etc.).  For those we still apply
+    //     the custom logic.
+    //   * Emit a warning for any variable that is not in the whitelist so that
+    //     developers are aware that the plugin does not have special handling
+    //     for it.
+    // -------------------------------------------------------------------------
+
+    // Variables that need special processing before being written to the values
+    // file.  The map value is a placeholder for f

=== TASK kytos__python-openflow-280 | repo kytos/python-openflow | python
--- Issue
OF 1.3 Compliance: Missing attribute
File: `v0x04/controller2switch/common.py`

`class DecStats(GenericStruct):`

Following attribute is missing:
`dp_desc = Char(length=DESC_STR_LEN)`
--- REAL FIX (ground truth)
diff --git a/pyof/v0x04/common/action.py b/pyof/v0x04/common/action.py
index 9b550a2..78d6004 100644
--- a/pyof/v0x04/common/action.py
+++ b/pyof/v0x04/common/action.py
@@ -72,7 +72,6 @@ class ActionType(Enum):
     OFPAT_POP_PBB = 27
     #: Experimenter type
     OFPAT_EXPERIMENTER = 0xffff
-    OFPAT_VENDOR = 0xffff
 
 
 class ControllerMaxLen(Enum):
@@ -254,6 +253,8 @@ class ActionPopMPLS(GenericStruct):
     length = UBInt16(8)
     #: Ethertype
     ethertype = UBInt16()
+    #: Padding
+    pad = Pad(2)
 
     def __init__(self, ethertype=None):
         """Action structure for OFPAT_POP_MPLS.
diff --git a/pyof/v0x04/common/port.py b/pyof/v0x04/common/port.py
index db3b41c..41b5be3 100644
--- a/pyof/v0x04/common/port.py
+++ b/pyof/v0x04/common/port.py
@@ -44,7 +44,7 @@ class PortNo(Enum):
     #: Wildcard port used only for flow mod (delete) and flow stats requests.
     #: Selects all flows regardless of output port (including flows with no
     #: output port).
-    OFPP_NONE = 0xffffffff
+    OFPP_ANY = 0xffffffff
 
 
 class PortConfig(GenericBitMask):
diff --git a/pyof/v0x04/controller2switch/common.py b/pyof/v0x04/controller2switch/common.py
index bebb8b2..42a4a46 100644
--- a/pyof/v0x04/controller2switch/common.py
+++ b/pyof/v0x04/controller2switch/common.py
@@ -251,9 +251,11 @@ class DescStats(GenericStruct):
     sw_desc = Char(length=DESC_STR_LEN)
     #: Serial number
     serial_num = Char(length=SERIAL_NUM_LEN)
+    #: Datapath description
+    dp_desc = Char(length=DESC_STR_LEN)
 
     def __init__(self, mfr_desc=None, hw_desc=None, sw_desc=None,
-                 serial_num=None):
+                 serial_num=None, dp_desc=None):
         """The constructor just assings parameters to object attributes.
 
         Args:
@@ -261,12 +263,14 @@ class DescStats(GenericStruct):
             hw_desc (str): Hardware description
             sw_desc (str): Software description
             serial_num (str): Serial number
+            dp_desc (str): Datapath description
         """
         super().__init__()
         self.mfr_desc = mfr_desc
         self.hw_desc = hw_desc
         self.sw_desc = sw_desc
         self.serial_num = serial_num
+        self.dp_desc = dp_desc
 
 
 class FlowStats(GenericStruct):
diff --git a/pyof/v0x04/controller2switch/packet_out.py b/pyof/v0x04/controller2switch/packet_out.py
index e23fc87..1520f85 100644
--- a/pyof/v0x04/controller2switch/packet_out.py
+++ b/pyof/v0x04/controller2switch/packet_out.py
@@ -13,7 +13,7 @@ __all__ = ('PacketOut',)
 # Classes
 
 #: in_port valid virtual port values, for validation
-_VIRT_IN_PORTS = (PortNo.OFPP_LOCAL, PortNo.OFPP_CONTROLLER, PortNo.OFPP_NONE)
+_VIRT_IN_PORTS = (PortNo.OFPP_LOCAL, PortNo.OFPP_CONTROLLER, PortNo.OFPP_ANY)
 
 
 class PacketOut(GenericMessage):

--- Proposal A
## Root cause
`DecStats` (the structure that represents the *Description* multipart reply for OpenFlow 1.3) is missing the `dp_desc` field.  
According to the OF 1.3 specification the description stats must contain **five** string fields, each of length `DESC_STR_LEN`:

```
mfr_desc, hw_desc, sw_desc, serial_num, dp_desc
```

Only the first four were defined, so attempts to pack/unpack a description reply raised an `AttributeError` or produced malformed binary data.

## Fix
Add the missing `dp_desc` attribute to `DecStats` in the correct order (last, after `serial_num`).

```diff
--- a/kytos/of_core/v0x04/controller2switch/common.py
+++ b/kytos/of_core/v0x04/controller2switch/common.py
@@
 class DecStats(GenericStruct):
     """Description statistics."""
-    mfr_desc = Char(length=DESC_STR_LEN)
-    hw_desc = Char(length=DESC_STR_LEN)
-    sw_desc = Char(length=DESC_STR_LEN)
-    serial_num = Char(length=DESC_STR_LEN)
+    mfr_desc   = Char(length=DESC_STR_LEN)
+    hw_desc    = Char(length=DESC_STR_LEN)
+    sw_desc    = Char(length=DESC_STR_LEN)
+    serial_num = Char(length=DESC_STR_LEN)
+    # NOTE: OpenFlow 1.3 adds a datapath description field that was
+    # previously omitted.  It must appear **after** `serial_num` and
+    # have the same fixed length as the other description strings.
+    dp_desc    = Char(length=DESC_STR_LEN)
```

* No other files need to be changed because the constant `DESC_STR_LEN` and the `Char` descriptor are already imported in this module.

## How to verify
1. **Run the existing test suite** – especially the tests that exercise multipart description messages (`test_desc_stats_*` or similar). All should now pass.

   ```bash
   pytest -q kytos/of_core/tests/v0x04/test_controller2switch_common.py
   ```

2. **Manual pack/unpack sanity check**:

   ```python
   from kytos.of_core.v0x04.controller2switch.common import DecStats
   from kytos.of_core.constants import DESC_STR_LEN

   # Build a DecStats instance with all fields filled
   stats = DecStats(
       mfr_desc='MFR',
       hw_desc='HW',
       sw_desc='SW',
       serial_num='12345',
       dp_desc='DP'
   )
   packed = stats.pack()
   unpacked = DecStats.unpack(packed)

   assert unpacked.dp_desc == 'DP'
   ```

   The assertion must succeed and the packed length must be `5 * DESC_STR_LEN`.

3. **Check binary compatibility** – compare the packed bytes against a reference dump from an OpenFlow 1.3 switch (or a known‑good implementation). The last `DESC_STR_LEN` bytes should contain the datapath description.

If all the above steps succeed, the missing attribute issue is resolved.
--- Proposal B
## Root cause
`DecStats` (the structure that represents *Description Statistics* in the OpenFlow 1.3
controller‑to‑switch messages) is missing the `dp_desc` field.  
According to the OF 1.3 specification the description stats must contain the
following string fields (all of length `DESC_STR_LEN`):

* `mfr_desc`
* `hw_desc`
* `sw_desc`
* `serial_num`
* **`dp_desc`** – datapath description

Because the attribute is absent, any attempt to serialize or deserialize a
`DecStats` instance that includes a datapath description fails, breaking OF 1.3
compliance tests.

## Fix
Add the missing attribute to the `DecStats` definition and import the constant
`DESC_STR_LEN` if it is not already in the module’s namespace.

```diff
--- a/kytos/python-openflow/kytos/of/core/v0x04/controller2switch/common.py
+++ b/kytos/python-openflow/kytos/of/core/v0x04/controller2switch/common.py
@@
 from pyof.foundation.base import GenericStruct
 from pyof.foundation.basic_types import Char, UBInt32, UBInt64, FixedTypeList
 from pyof.v0x04.common.constants import OFP_MAX_TABLE_NAME_LEN, OFP_MAX_PORT_NAME_LEN
+from pyof.v0x04.common.constants import DESC_STR_LEN   # <-- added import
@@
 class DecStats(GenericStruct):
@@
     mfr_desc = Char(length=DESC_STR_LEN)
     hw_desc = Char(length=DESC_STR_LEN)
     sw_desc = Char(length=DESC_STR_LEN)
     serial_num = Char(length=DESC_STR_LEN)
+    dp_desc = Char(length=DESC_STR_LEN)   # <-- added missing attribute
     n_buffers = UBInt32()
     n_tables = UBInt8()
     capabilities = UBInt32()
```

* If `DESC_STR_LEN` is already imported elsewhere in the file, the added import
  line can be omitted – the diff keeps it safe by explicitly importing it.

## How to verify
1. **Run the project's test suite** – especially the OF 1.3 compliance tests
   (`tests/of/v0x04/...`). All tests should now pass.

2. **Manual serialization check**  
   ```python
   from kytos.of.core.v0x04.controller2switch.common import DecStats
   from kytos.of.core.v0x04.common.constants import DESC_STR_LEN

   ds = DecStats()
   ds.dp_desc = "My datapath"
   raw = ds.pack()
   # unpack again
   ds2 = DecStats()
   ds2.unpack(raw)
   assert ds2.dp_desc == "My datapath"
   ```
   The pack/unpack round‑trip must succeed without raising an exception.

3. **OpenFlow 1.3 compliance validation** – use a known OF 1.3‑compliant
   controller (e.g., `ryu` or `of-test`) to request description stats from a
   Kytos switch instance. The response should now contain the `dp_desc` field
   correctly populated.

After these steps the missing attribute issue is resolved and the library
conforms to the OF 1.3 specification.

=== TASK doctrine__orm-11826 | repo doctrine/orm | php
--- Issue
Fetch mode hint is ignored when using `Paginator` 
### Bug Report

<!-- Fill in the relevant information below to help triage your issue. -->

|    Q                                        |   A
|-------------------------------------------- | --------------
| Version                                     | 2.20.0 / 3.3.0
| Previous working version                    | 2.19.8 / 3.2.3

#### Summary

This is related to issue #11128 and PR #11445.

When a `fetch=EAGER` association is filtered using `WITH`, it raises an error unless fetch mode is overridden by calling `setFetchMode(LAZY)` over the association.

This worked well after the fix in #11445.

Somehow, in 2.20.x and 3.3.x this bug returned, partially, only when using `Paginator` with `fetchJoinCollection` parameter set to `true`.

Seems like #11188 introduced this bug.


#### Current behavior

An exception is raised even when fetch mode is overridden.

#### Expected behavior

It should work the same with or without pagination.

#### How to reproduce

See test in https://github.com/aprat84/orm/tree/gh-11741

```
vendor/bin/phpunit --filter testSubselectFetchJoinWithAllowedWhenOverriddenNotEagerPaginator

There was 1 error:

1) Doctrine\Tests\ORM\Functional\EagerFetchCollectionTest::testSubselectFetchJoinWithAllowedWhenOverriddenNotEagerPaginator
Doctrine\ORM\Query\QueryException: Associations with fetch-mode=EAGER may not be using WITH conditions in "Doctrine\Tests\ORM\Functional\Eage
--- REAL FIX (ground truth)
diff --git a/src/Persisters/Entity/BasicEntityPersister.php b/src/Persisters/Entity/BasicEntityPersister.php
index 2e905bca7..4073c606e 100644
--- a/src/Persisters/Entity/BasicEntityPersister.php
+++ b/src/Persisters/Entity/BasicEntityPersister.php
@@ -1374,12 +1374,6 @@ class BasicEntityPersister implements EntityPersister
                     $joinCondition[] = $this->getSQLTableAlias($association['sourceEntity'], $assocAlias) . '.' . $sourceCol . ' = '
                         . $this->getSQLTableAlias($association['targetEntity']) . '.' . $targetCol;
                 }
-
-                // Add filter SQL
-                $filterSql = $this->generateFilterConditionSQL($eagerEntity, $joinTableAlias);
-                if ($filterSql) {
-                    $joinCondition[] = $filterSql;
-                }
             }
 
             $this->currentPersisterContext->selectJoinSql .= ' ' . $joinTableName . ' ' . $joinTableAlias . ' ON ';
diff --git a/src/Tools/Pagination/LimitSubqueryOutputWalker.php b/src/Tools/Pagination/LimitSubqueryOutputWalker.php
index 95b9066db..ea7ec0f63 100644
--- a/src/Tools/Pagination/LimitSubqueryOutputWalker.php
+++ b/src/Tools/Pagination/LimitSubqueryOutputWalker.php
@@ -105,17 +105,24 @@ class LimitSubqueryOutputWalker extends SqlOutputWalker
         $this->platform = $query->getEntityManager()->getConnection()->getDatabasePlatform();
         $this->rsm      = $parserResult->getResultSetMapping();
 
-        $query = clone $query;
+        $cloneQuery = clone $query;
+
+        $cloneQuery->setParameters(clone $query->getParameters());
+        $cloneQuery->setCacheable(false);
+
+        foreach ($query->getHints() as $name => $value) {
+            $cloneQuery->setHint($name, $value);
+        }
 
         // Reset limit and offset
-        $this->firstResult = $query->getFirstResult();
-        $this->maxResults  = $query->getMaxResults();
-        $query->setFirstResult(0)->setMaxResults(null);
+        $this->firstResult = $cloneQuery->getFirstResult();
+        $this->maxResults  = $cloneQuery->getMaxResults();
+        $cloneQuery->setFirstResult(0)->setMaxResults(null);
 
-        $this->em            = $query->getEntityManager();
+        $this->em            = $cloneQuery->getEntityManager();
         $this->quoteStrategy = $this->em->getConfiguration()->getQuoteStrategy();
 
-        parent::__construct($query, $parserResult, $queryComponents);
+        parent::__construct($cloneQuery, $parserResult, $queryComponents);
     }
 
     /**

--- Proposal A
## Root cause
`Paginator` builds a separate **count query** by cloning the original `Query`.  
During this cloning the **fetch‑mode hint** (`Query::HINT_FETCH_MODE`) that is used to
override an eager association is **not copied** to the count query.  
When the count query is parsed Doctrine still sees the association with
`fetch=EAGER` together with a `WITH` clause and throws  

```
Associations with fetch-mode=EAGER may not be using WITH conditions …
```

The main query works because its hint is present, but the count query (executed
by the paginator when `$fetchJoinCollection = true`) fails, causing the whole
pagination to break.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/src/Doctrine/ORM/Tools/Pagination/Paginator.php
+++ b/src/Doctrine/ORM/Tools/Pagination/Paginator.php
@@
 use Doctrine\ORM\Query;
 use Doctrine\ORM\Query\ResultSetMapping;
 use Doctrine\ORM\Tools\Pagination\CountWalker;
+use Doctrine\ORM\Query\ResultSetMappingBuilder;
 
 /**
  * Paginator implementation.
  *
  * @author Benjamin Eberlei <kontakt@beberlei.de>
  */
 class Paginator implements \IteratorAggregate, \Countable
@@
     /**
      * Returns the count query used internally.
      *
      * @return Query
      */
     private function getCountQuery()
     {
-        $countQuery = clone $this->query;
-        $countQuery->setFirstResult(null)
-                   ->setMaxResults(null);
-
-        $countQuery->setHint(Query::HINT_CUSTOM_OUTPUT_WALKER, CountWalker::class);
-
-        return $countQuery;
+        // Clone the original query that the user supplied.
+        $countQuery = clone $this->query;
+
+        // The count query must not have pagination limits.
+        $countQuery->setFirstResult(null)
+                   ->setMaxResults(null);
+
+        // Use the special CountWalker that rewrites the SELECT clause.
+        $countQuery->setHint(Query::HINT_CUSTOM_OUTPUT_WALKER, CountWalker::class);
+
+        // -----------------------------------------------------------------
+        // BUGFIX: Preserve the fetch‑mode hint.
+        // -----------------------------------------------------------------
+        // The original query may contain a fetch‑mode override (e.g.
+        // $query->setFetchMode(..., FetchMode::LAZY)).  When the paginator
+        // creates the count query this hint was lost, causing Doctrine to
+        // validate the original eager mapping and throw an exception for
+        // associations that use a WITH clause.  We copy the hint explicitly
+        // so that the count query is parsed with the same fetch‑mode
+        // configuration as the main query.
+        if ($this->query->hasHint(Query::HINT_FETCH_MODE)) {
+            $countQuery->setHint(
+                Query::HINT_FETCH_MODE,
+                $this->query->getHint(Query::HINT_FETCH_MODE)
+            );
+        }
+
+        return $countQuery;
     }
```

### What was changed?
1. **Cloned the original query** (unchanged) but now explicitly copies the
   `HINT_FETCH_MODE` hint from the original query to the count query.
2. Added a detailed comment explaining why the hint must be propagated.
3. Imported the `Query` class (already present) – no new imports required.

## How to verify
1. **Run the failing functional test** that reproduces the issue:

   ```bash
   vendor/bin/phpunit --filter testSubselectFetchJoinWithAllowedWhenOverriddenNotEagerPaginator
   ```

   The test should now pass without throwing `QueryException`.

--- Proposal B
**## Root cause**

`Doctrine\ORM\Tools\Pagination\Paginator` creates a *count* query by cloning the original `Query` object and then adds its own pagination‑specific hints (e.g. `Query::HINT_CUSTOM_OUTPUT_WALKER`).  
During this process the **fetch‑mode hint** (`Query::HINT_FETCH_MODE`) that may have been set on the original query is **lost** because the paginator overwrites the whole hint array when it sets its own hint.  

When the original query contains an association that is eager‑fetched but is filtered with a `WITH` clause, Doctrine validates the query and throws  

```
Associations with fetch-mode=EAGER may not be using WITH conditions …
```

If the fetch‑mode hint is correctly present (e.g. overridden to `LAZY` via `setFetchMode()`), the validation would be bypassed.  
Because the paginator discards the hint, the validation sees the mapping’s default eager fetch mode and the exception is raised. This only happens when the paginator is used with `fetchJoinCollection = true`, which is why the bug appears only in that scenario.

**## Fix (a unified diff with your best‑guess file paths and code)**

```diff
--- a/src/Doctrine/ORM/Tools/Pagination/Paginator.php
+++ b/src/Doctrine/ORM/Tools/Pagination/Paginator.php
@@
 class Paginator implements \IteratorAggregate, \Countable
 {
@@
     public function __construct(Query $query, $fetchJoinCollection = true)
     {
         $this->query               = $query;
         $this->fetchJoinCollection = (bool) $fetchJoinCollection;
-
-        // Force partial objects to avoid loading the whole collection when
-        // pagination is used. This hint is required for the LimitSubselectWalker.
-        $this->query->setHint(Query::HINT_FORCE_PARTIAL_LOAD, true);
+        // Force partial objects to avoid loading the whole collection when
+        // pagination is used. This hint is required for the LimitSubselectWalker.
+        $this->query->setHint(Query::HINT_FORCE_PARTIAL_LOAD, true);
+
+        // -----------------------------------------------------------------
+        // Preserve fetch‑mode hint for the paginator’s internal count query.
+        // -----------------------------------------------------------------
+        // The paginator creates a clone of the original query for the count
+        // operation and then adds its own pagination‑specific hints.  The
+        // original implementation overwrote the whole hint array, which
+        // unintentionally removed the fetch‑mode hint (`Query::HINT_FETCH_MODE`).
+        // When an association is eager‑fetched but filtered with a WITH clause,
+        // Doctrine validates the query and throws an exception unless the
+        // fetch mode is explicitly overridden.  By copying the fetch‑mode hint
+        // to the cloned count query we ensure that the overridden mode is
+        // respected.
+        // -----------------------------------------------------------------
+        $this->originalFetchModeHint = $this->query->getHint(Query::HINT_FETCH_MODE);
     }
@@
     public function getIterator()
     {
         $this->initialize();
@@
     private function initialize()
     {
         if ($this->initialized) {
             return;
         }
@@
-        $countQuery = clone $this->query;
-        $countQuery->setFirstResult(null)
-                   ->setMaxResults(null);
-
-        $countQuery->setHint(Query::HINT_CUSTOM_OUTPUT_WALKER, CountWalker::class);
-
-        $this->count = (int) $countQuery->getSingleScalarResult();


=== TASK tomerfiliba__plumbum-366 | repo tomerfiliba/plumbum | python
--- Issue
SyntaxError in plumbum/cli/image.py
image.py contains the line

import . as cli

All versions of python I tried (3.6, 3.5, 2.7) raise SyntaxError reading this line. According to the Python Language Reference, such syntax of the import statement is not allowed. What exactly is the purpose of this line? And how to achieve this purpose in legal python?
--- REAL FIX (ground truth)
diff --git a/plumbum/cli/image.py b/plumbum/cli/image.py
index b207fa3..502af80 100644
--- a/plumbum/cli/image.py
+++ b/plumbum/cli/image.py
@@ -2,7 +2,7 @@ from __future__ import print_function, division
 
 from plumbum import colors
 from .termsize import get_terminal_size
-import . as cli
+from .. import cli
 import sys
 
 class Image(object):

--- Proposal A
## Root cause
`plumbum/cli/image.py` contains the line  

```python
import . as cli
```  

which is not valid Python syntax. The intention was to import the sibling module `cli` (i.e. the package `plumbum.cli`) and bind it to the name `cli` for later use. The correct way to perform a relative import of a sibling module is `from . import cli` (or `from . import cli as cli`). The erroneous line raises a `SyntaxError` on every Python version, preventing the module (and any code that imports it) from being loaded.

## Fix
```diff
--- a/plumbum/cli/image.py
+++ b/plumbum/cli/image.py
@@
-import . as cli
+# Import the sibling ``cli`` module (the package that contains this file).
+# The original code used the illegal syntax ``import . as cli`` which raises a
+# SyntaxError on all Python versions.  The correct relative import is
+# ``from . import cli``; the imported module is bound to the name ``cli`` so
+# the rest of the file can continue to refer to it unchanged.
+from . import cli
```

*Explanation of the change*  
- Replaced the illegal statement with a proper relative import.  
- Added a short comment explaining why the change is needed and what the import does.  
- No other code changes are required because the imported module is still available under the name `cli`.

## How to verify
1. **Import test** – In a fresh interpreter (or a test script) run:  

   ```python
   from plumbum.cli import image
   ```  

   It should import without raising `SyntaxError`.

2. **Run the library test suite** – Execute the project's test suite (e.g. `pytest -q`). All tests should pass, and there should be no import‑related failures.

3. **Functional sanity check** – If the library provides any CLI commands that rely on `image.py`, invoke one of them (e.g. `python -m plumbum.cli.image ...`) and verify that it behaves as expected.

4. **Static analysis** – Run `flake8` or `pylint` on the repository to ensure no new linting errors are introduced.

Successful completion of the above steps confirms that the SyntaxError is resolved and the module works correctly.
--- Proposal B
## Root cause
`plumbum/cli/image.py` contains the line  

```python
import . as cli
```  

which is not valid Python syntax. The author intended to import the **`plumbum.cli` package** (i.e. its `__init__` module) and bind it to the name `cli` so that the rest of the file can refer to symbols defined in `plumbum.cli.__init__`. Python’s import statement does not allow a bare dot (`.`) to be used as a module name, so every interpreter raises a `SyntaxError` when parsing the file.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/plumbum/cli/image.py
+++ b/plumbum/cli/image.py
@@
-# The original (invalid) import – raises SyntaxError on all Python versions
-import . as cli
+# Import the top‑level ``plumbum.cli`` package and expose it under the name ``cli``.
+# This mirrors the intent of the original (invalid) statement while using legal
+# Python syntax.  An absolute import is used to avoid any ambiguity with relative
+# imports inside the package.
+import plumbum.cli as cli
```

*Explanation of the change*  
- The erroneous line is removed.  
- An explicit absolute import `import plumbum.cli as cli` correctly loads the package’s `__init__` module and binds it to the name `cli`, preserving the original module’s public API usage throughout `image.py`.

If the project prefers relative imports, the equivalent would be:

```python
from .. import cli as cli   # relative to the top‑level ``plumbum`` package
```

but the absolute form is clearer and works regardless of the package’s `__package__` value.

## How to verify
1. **Run the test suite** (if the project provides one):
   ```bash
   python -m pytest
   ```
   All tests should now import `plumbum.cli.image` without raising a `SyntaxError`.

2. **Manual import check**:
   ```bash
   python - <<'PY'
   import plumbum.cli.image
   print("Import succeeded, cli alias:", plumbum.cli.image.cli)
   PY
   ```
   The script should print the module object for `plumbum.cli` and exit without error.

3. **Static analysis** (optional):
   ```bash
   python -m py_compile $(git ls-files '*.py')
   ```
   No syntax errors should be reported.

4. **Runtime behavior**: Execute any example that uses the image CLI (e.g., the `plumbum` command‑line utilities that rely on `plumbum.cli.image`). They should function as before, confirming that the alias `cli` correctly references the package.

=== TASK orchestr7__ktoml-304 | repo orchestr7/ktoml | kotlin
--- Issue
Does ktoml support `Map<String, T>` field types?
Hi, I'm trying to deserialize a TOML like this:

```
[coins.bitcoin]
scale = 8
default_volume = 0.1
skip_in_orderbook = 0.00001

[coins.ethereum]
scale = 8
default_volume = 0.2
skip_in_orderbook = 0.00001

[coins.tether]
scale = 2
default_volume = 100
skip_in_orderbook = 0.0001
```

Here's my structure:

```
@Serializable
data class Coin(
    val scale: Long,
    @SerialName("default_volume")
    val defaultVolume: Double,
    @SerialName("skip_in_orderbook")
    val skipInOrderbook: Double,
)

@Serializable
data class CoinsConf(
    val coins: Map<String, Coin>
)
```

I'm getting this error:

```
Unexpected exception thrown: com.akuleshov7.ktoml.exceptions.MissingRequiredPropertyException: Invalid number of key-value arguments provided in the input for deserialization. Missing required property <0> from class <kotlin.collections.LinkedHashMap> in the input
```

Is this a bug, or ktoml doesn't support `Map`?

For reference, this code in Rust using serde works just fine:

```
#[derive(Deserialize, Debug)]
pub struct Coin {
    pub scale: u32,
    pub default_volume: f64,
    pub skip_in_orderbook: f64,
}

#[derive(Deserialize, Debug)]
pub struct CoinsConfig {
    coins: HashMap<String, Coin>,
}
```
--- REAL FIX (ground truth)
diff --git a/ktoml-core/src/commonMain/kotlin/com/akuleshov7/ktoml/decoders/TomlMapDecoder.kt b/ktoml-core/src/commonMain/kotlin/com/akuleshov7/ktoml/decoders/TomlMapDecoder.kt
index c47df412..8509501c 100644
--- a/ktoml-core/src/commonMain/kotlin/com/akuleshov7/ktoml/decoders/TomlMapDecoder.kt
+++ b/ktoml-core/src/commonMain/kotlin/com/akuleshov7/ktoml/decoders/TomlMapDecoder.kt
@@ -9,6 +9,7 @@ import com.akuleshov7.ktoml.tree.nodes.pairs.keys.TomlKey
 import kotlinx.serialization.DeserializationStrategy
 import kotlinx.serialization.ExperimentalSerializationApi
 import kotlinx.serialization.descriptors.SerialDescriptor
+import kotlinx.serialization.descriptors.StructureKind
 import kotlinx.serialization.encoding.CompositeDecoder
 import kotlinx.serialization.modules.EmptySerializersModule
 import kotlinx.serialization.modules.SerializersModule
@@ -95,7 +96,13 @@ public class TomlMapDecoder private constructor(
             is TomlTable -> if (index % 2 == 0) {
                 processedNode.name as T
             } else {
-                TomlMapDecoder(processedNode, config).decodeSerializableValue(deserializer)
+                if (deserializer.descriptor.kind == StructureKind.CLASS) {
+                    var rootNode = TomlFile()
+                    rootNode.children.addAll(processedNode.children)
+                    TomlMainDecoder.decode(deserializer, rootNode, config)
+                } else {
+                    TomlMapDecoder(processedNode, config).decodeSerializableValue(deserializer)
+                }
             }
             else -> throw InternalDecodingException("Trying to decode ${processedNode.prettyStr()} with TomlMapDecoder, " +
                     "but faced an unknown type of Node")

--- Proposal A
## Root cause
`ktoml`’s decoder treats a TOML table that represents a `Map<String, T>` as a **regular class** instead of a map.  
When the parser sees the dotted‑key tables

```toml
[coins.bitcoin]
...
[coins.ethereum]
...
```

it builds a nested `TomlTable` structure:

```
coins → {
    bitcoin  → {scale = …, default_volume = …, …}
    ethereum → {…}
    tether   → {…}
}
```

The decoder’s `decodeMap` implementation only handles *inline* tables (e.g. `coins = { bitcoin = {...}, ethereum = {...} }`).  
Because the decoder does not recognise that the values of the top‑level `coins` table are themselves tables, it falls back to the generic class‑decoder path, which expects properties named `0`, `1`, … and therefore throws

```
MissingRequiredPropertyException: Invalid number of key‑value arguments …
```

In short, **`ktoml` lacked support for maps whose values are complex tables** (the common TOML pattern of “table of tables”).

---

## Fix
Add proper handling for map deserialization when the current node is a `TomlTable` whose entries are tables (or any other serializable values).  
The fix lives in the decoder implementation – `TomlDecoder.kt`.

```diff
--- a/src/main/kotlin/com/akuleshov7/ktoml/decoder/TomlDecoder.kt
+++ b/src/main/kotlin/com/akuleshov7/ktoml/decoder/TomlDecoder.kt
@@
 class TomlDecoder(
     private val toml: TomlNode,
     private val configuration: TomlConfiguration,
 ) : Decoder, CompositeDecoder {
@@
     override fun decodeSerializableValue(serializer: SerializationStrategy<Any?>): Any? {
-        // existing implementation …
+        // --------------------------------------------------------------------
+        // New: proper handling of Map<String, T> where the map values are
+        // represented as nested tables (the “table of tables” pattern).
+        // --------------------------------------------------------------------
+        if (serializer.descriptor.kind == StructureKind.MAP) {
+            // The descriptor for a map always has two element descriptors:
+            // 0 – key, 1 – value
+            val keyDescriptor = serializer.descriptor.getElementDescriptor(0)
+            val valueDescriptor = serializer.descriptor.getElementDescriptor(1)
+
+            // The current node must be a TomlTable that contains the map entries.
+            val table = currentNode as? TomlTable
+                ?: throw IllegalStateException(
+                    "Expected a TomlTable for map deserialization, but got ${currentNode::class}"
+                )
+
+            // Prepare a mutable map that matches the expected Kotlin type.
+            val result = mutableMapOf<Any?, Any?>()
+
+            // Iterate over each entry of the table.  Keys are always strings in TOML,
+            // but we still run them through the key serializer to respect possible
+            // custom serializers (e.g. enum keys).
+            for ((rawKey, rawValue) in table.entries) {
+                // ---- decode key ------------------------------------------------
+                // Keys in TOML tables are always strings, so we can safely cast.
+                // If a custom key serializer is supplied we delegate to it.
+                val decodedKey = when (keyDescriptor.kind) {
+                    PrimitiveKind.STRING -> rawKey
+                    else -> {
+                        // Push a temporary node that contains the raw key as a scalar.
+                        pushNode(TomlLiteral(rawKey))
+                   
--- Proposal B
## Root cause
`ktoml` always routed deserialization through `TomlMainDecoder`, even when the target type was a `Map`.  
`TomlMainDecoder` expects a regular class descriptor and therefore tried to read map entries as ordinary properties (`<0>`, `<1>` …). This produced the *MissingRequiredPropertyException* shown in the issue.  
The library already contains a `TomlMapDecoder` that can decode `Map<String, …>` values, but it was never used for nested map fields (e.g. `CoinsConf.coins: Map<String, Coin>`).

## Fix
Add a small dispatch layer that selects the proper decoder based on the serializer’s descriptor kind (`StructureKind.MAP`).  
The change is limited to the public `decodeFromString` helpers – no existing decoder logic is altered.

```diff
--- a/src/main/kotlin/com/akuleshov7/ktoml/Toml.kt
+++ b/src/main/kotlin/com/akuleshov7/ktoml/Toml.kt
@@
-import kotlinx.serialization.KSerializer
-import kotlinx.serialization.serializer
+import kotlinx.serialization.KSerializer
+import kotlinx.serialization.serializer
+import kotlinx.serialization.descriptors.StructureKind
+import com.akuleshov7.ktoml.decoder.TomlMainDecoder
+import com.akuleshov7.ktoml.decoder.TomlMapDecoder
@@
-inline fun <reified T> Toml.decodeFromString(toml: String): T =
-    decodeFromString(T::class.serializer(), toml)
+/**
+ * Decode a TOML string into an object of type [T].
+ *
+ * The function now detects whether the requested type is a `Map` and, if so,
+ * delegates the work to `TomlMapDecoder`.  For all other kinds it falls back
+ * to the regular `TomlMainDecoder`.
+ */
+inline fun <reified T> Toml.decodeFromString(toml: String): T =
+    decodeFromString(T::class.serializer(), toml)
 
-fun <T> Toml.decodeFromString(deserializer: KSerializer<T>, toml: String): T {
-    val tomlFile = TomlFile(toml)
-    return TomlMainDecoder(tomlFile).decode(deserializer)
-}
+/**
+ * Core decoding entry point used by the public overloads.
+ *
+ * It decides which decoder implementation should be used based on the
+ * descriptor kind of the supplied [deserializer].
+ */
+fun <T> Toml.decodeFromString(deserializer: KSerializer<T>, toml: String): T {
+    val tomlFile = TomlFile(toml)
+
+    // Choose the appropriate decoder:
+    //   - MAP   → TomlMapDecoder (handles Map<String, *>)
+    //   - other → TomlMainDecoder (regular class/structure decoding)
+    val decoder = if (deserializer.descriptor.kind == StructureKind.MAP) {
+        TomlMapDecoder(tomlFile)
+    } else {
+        TomlMainDecoder(tomlFile)
+    }
+
+    @Suppress("UNCHECKED_CAST")
+    return (decoder as com.akuleshov7.ktoml.decoder.TomlDecoder).decode(deserializer)
+}
```

*Explanation of the changes*

1. **Import `StructureKind`** – needed to recognise map descriptors.
2. **Add documentation** – clarifies the new dispatch behaviour.
3. **Replace the old direct call to `TomlMainDecoder`** with a conditional that creates either a `TomlMapDecoder` (when the descriptor kind is `MAP`) or a `TomlMainDecoder` (otherwise).
4. **Cast the chosen decoder to the common `TomlDecoder` interface** (both decoders implement it) and invoke `decode(deserializer)`.

No modifications to `TomlMainDecoder` or `TomlMapDecoder` are required because they already implement the `TomlDecoder` contract.

## How to verify
1. **Add a unit test** (or run the snippet below) that deserialises the TOML from the issue:

```kotlin
import com.akuleshov7.ktoml.Toml
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName

@Ser

=== TASK vaskoz__dailycodingproblem-go-12 | repo vaskoz/dailycodingproblem-go | go
--- Issue
Day 5 extra - solve hard interview problems email
The question we'll work through is the following: **return a new sorted merged list from K sorted lists, each with size N.** Before we move on any further, you should take some time to think about the solution!
--- REAL FIX (ground truth)
diff --git a/README.md b/README.md
index 29f92b7..764ff76 100644
--- a/README.md
+++ b/README.md
@@ -14,4 +14,5 @@ problems from
 * [Day 3](https://github.com/vaskoz/dailycodingproblem-go/issues/7)
 * [Day 4](https://github.com/vaskoz/dailycodingproblem-go/issues/5)
 * [Day 5](https://github.com/vaskoz/dailycodingproblem-go/issues/9)
+* [Day 5 merge k sorted lists](https://github.com/vaskoz/dailycodingproblem-go/issues/11)
 
diff --git a/mergeKSortedLists/problem.go b/mergeKSortedLists/problem.go
new file mode 100644
index 0000000..be37516
--- /dev/null
+++ b/mergeKSortedLists/problem.go
@@ -0,0 +1,86 @@
+package mergeklist
+
+import (
+	"container/heap"
+	"math"
+)
+
+// MergeKSortedLists merges K-sorted lists into a single list
+// Runtime is O(K*(K*N)) = O(K^2*N) assuming each list of is length N
+// Space is O(K) for the indicies without considering the answer list of O(K*N) size.
+// Use this if K is very small
+func MergeKSortedLists(lists [][]int) []int {
+	idxs := make([]int64, len(lists))
+	var result []int
+	hasMore := true
+	for hasMore {
+		listID := -1
+		smallest := math.MaxInt64
+		// find the smallest next value
+		for i, idx := range idxs {
+			if idx < int64(len(lists[i])) && lists[i][idx] < smallest {
+				smallest = lists[i][idx]
+				listID = i
+			}
+		}
+		if listID == -1 {
+			hasMore = false
+		} else {
+			result = append(result, smallest)
+			idxs[listID]++
+		}
+	}
+	return result
+}
+
+type tuple struct {
+	id, pos, value int
+}
+
+type priorityQueue []*tuple
+
+func (pq priorityQueue) Len() int { return len(pq) }
+
+func (pq priorityQueue) Less(i, j int) bool {
+	return pq[i].value < pq[j].value
+}
+
+func (pq priorityQueue) Swap(i, j int) {
+	pq[i], pq[j] = pq[j], pq[i]
+}
+
+func (pq *priorityQueue) Push(x interface{}) {
+	item := x.(*tuple)
+	*pq = append(*pq, item)
+}
+
+func (pq *priorityQueue) Pop() interface{} {
+	old := *pq
+	n := len(old)
+	tuple := old[n-1]
+	*pq = old[0 : n-1]
+	return tuple
+}
+
+// MergeKSortedListsUsingHeap merges K-sorted lists into a single list using a min-heap.
+// Runtime is O(N*K log K) assuming each list of is length N
+// Space is O(K) for the heap without considering the answer list of O(K*N) size.
+// Use this if K is very large.
+func MergeKSortedListsUsingHeap(lists [][]int) []int {
+	pq := make(priorityQueue, len(lists))
+	for i, lst := range lists {
+		pq[i] = &tuple{id: i, pos: 0, value: lst[0]}
+	}
+	heap.Init(&pq)
+	var result []int
+	for pq.Len() > 0 {
+		tup := heap.Pop(&pq).(*tuple)
+		result = append(result, tup.value)
+		id := tup.id
+		nextpos := tup.pos + 1
+		if nextpos < len(lists[id]) {
+			heap.Push(&pq, &tuple{id: id, pos: nextpos, value: lists[id][nextpos]})
+		}
+	}
+	return result
+}

--- Proposal A
## Root cause
The repository is missing an implementation for the “Day 5 extra” problem: **merge K sorted lists (each of size N) into a single sorted list**. No Go source file provides the required `MergeKSortedLists` function, causing compilation errors for the tests that expect this functionality.

## Fix
Add a new Go source file that implements the missing function using a straightforward k‑way merge algorithm.

```diff
--- a/day005/merge_k_sorted_lists.go
+++ b/day005/merge_k_sorted_lists.go
@@
-// TODO: implement MergeKSortedLists
+package day005
+
+// MergeKSortedLists merges K sorted integer slices (each of size N) into a
+// single sorted slice. The function returns a new slice containing all the
+// elements from the input slices in non‑decreasing order.
+//
+// The implementation runs in O(K·N·log K) time using a simple k‑way merge
+// approach with a min‑heap. A heap is used to always pick the smallest
+// available element among the K lists, guaranteeing optimal performance while
+// keeping the code easy to understand.
+//
+// If the input slice is nil or contains only empty slices, an empty slice is
+// returned.
+func MergeKSortedLists(lists [][]int) []int {
+    // Edge case: no lists at all.
+    if len(lists) == 0 {
+        return []int{}
+    }
+
+    // Compute total number of elements to allocate the result slice once.
+    total := 0
+    for _, l := range lists {
+        total += len(l)
+    }
+    result := make([]int, 0, total)
+
+    // min‑heap entry: value and the index of the list it came from.
+    type entry struct {
+        val   int
+        listI int
+    }
+
+    // Simple binary heap implementation (min‑heap) for entries.
+    heap := make([]entry, 0, len(lists))
+
+    // indices[i] points to the next element to read from lists[i].
+    indices := make([]int, len(lists))
+
+    // Initialise heap with the first element of each non‑empty list.
+    for i, l := range lists {
+        if len(l) > 0 {
+            heap = append(heap, entry{val: l[0], listI: i})
+            indices[i] = 1 // next element to read from this list
+        }
+    }
+
+    // heapify (build min‑heap)
+    // standard sift‑down from the middle to the root.
+    var siftDown func(int)
+    siftDown = func(i int) {
+        n := len(heap)
+        for {
+            left := 2*i + 1
+            right := left + 1
+            smallest := i
+            if left < n && heap[left].val < heap[smallest].val {
+                smallest = left
+            }
+            if right < n && heap[right].val < heap[smallest].val {
+                smallest = right
+            }
+            if smallest == i {
+                break
+            }
+            heap[i], heap[smallest] = heap[smallest], heap[i]
+            i = smallest
+        }
+    }
+    for i := len(heap)/2 - 1; i >= 0; i-- {
+        siftDown(i)
+    }
+
+    // Helper to pop the smallest element from the heap.
+    pop := func() entry {
+        top := heap[0]
+        last := heap[len(heap)-1]
+        heap = heap[:len(heap)-1]
+        if len(heap) > 0 {
+            heap[0] = last
+            siftDown(0)
+        }
+        return top
+    }
+
+    // Helper to push a new entry onto the heap.
+    push := func(e entry) {
+        heap = append(heap, e)
+        // sift‑up
+        i := len(heap) - 1
+        for i > 0 {
+            parent := (i - 1) / 2
+            if heap[parent].val <= heap[i].val {
+                break
+            }
+            heap[pare
--- Proposal B
## Root cause
The repository contains a placeholder for the *Day 5 extra* problem – merging **K** sorted lists – but the function that should perform the merge is either missing or returns an empty result. Consequently the unit test that expects a correctly merged, sorted slice fails.

## Fix
Add a proper implementation that merges the K sorted slices using a min‑heap (the idiomatic O(N log K) solution). The implementation lives in `problems/day5_extra.go`.

```diff
--- a/problems/day5_extra.go
+++ b/problems/day5_extra.go
@@
 package problems
 
 import (
+    "container/heap"
 )
 
 // MergeKSortedLists merges K sorted integer slices into a single sorted slice.
 // Each inner slice is assumed to be sorted in non‑decreasing order.
 // The function returns a new slice containing all elements from the input
 // slices, also sorted in non‑decreasing order.
 //
 // Expected time complexity: O(N·log K) where N = K * size of each list.
 // Expected space complexity: O(N) for the output slice plus O(K) for the heap.
 func MergeKSortedLists(lists [][]int) []int {
-    // TODO: implement
-    return nil
+    // Edge cases: no lists or all lists empty.
+    if len(lists) == 0 {
+        return []int{}
+    }
+
+    // Initialise a min‑heap that will store (value, listIndex, elementIndex).
+    h := &intHeap{}
+    heap.Init(h)
+
+    // Total number of elements to allocate the result slice once.
+    total := 0
+    for i, lst := range lists {
+        if len(lst) > 0 {
+            // Push the first element of each non‑empty list.
+            heap.Push(h, heapItem{value: lst[0], listIdx: i, elemIdx: 0})
+            total += len(lst)
+        }
+    }
+
+    // Result slice pre‑allocated to the exact size.
+    result := make([]int, 0, total)
+
+    // Extract the smallest element, then push the next element from the same list.
+    for h.Len() > 0 {
+        item := heap.Pop(h).(heapItem)
+        result = append(result, item.value)
+
+        nextIdx := item.elemIdx + 1
+        if nextIdx < len(lists[item.listIdx]) {
+            heap.Push(h, heapItem{
+                value:   lists[item.listIdx][nextIdx],
+                listIdx: item.listIdx,
+                elemIdx: nextIdx,
+            })
+        }
+    }
+
+    return result
 }
 
 // ---------------------------------------------------------------------------
 // Helper types for the min‑heap
 // ---------------------------------------------------------------------------
 
 type heapItem struct {
     value   int
     listIdx int // which input list this value came from
     elemIdx int // index within that list
 }
 
 type intHeap []heapItem
@@
 func (h intHeap) Len() int { return len(h) }
 func (h intHeap) Less(i, j int) bool { return h[i].value < h[j].value }
 func (h intHeap) Swap(i, j int) { h[i], h[j] = h[j], h[i] }
 
 func (h *intHeap) Push(x interface{}) {
-    // TODO: Please implement
+    *h = append(*h, x.(heapItem))
 }
 
 func (h *intHeap) Pop() interface{} {
-    // TODO: Please implement
-    panic("Not implemented")
+    old := *h
+    n := len(old)
+    item := old[n-1]
+    *h = old[0 : n-1]
+    return item
 }
```

### What was added
1. **Full implementation** of `MergeKSortedLists` using a min‑heap.
2. **Heap helper types** (`heapItem`, `intHeap`) with proper `Push` and `Pop` methods.
3. Necessary import of `container/heap`.
4. Edge‑case handling and pre‑allocation of the result slice for efficiency.

## How to verify
1. **Run the test suite**  
   ```bash
   go te