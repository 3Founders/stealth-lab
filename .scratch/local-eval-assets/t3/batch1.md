

=== TASK obsidian-tasks-group__obsidian-tasks-2486 | repo obsidian-tasks-group/obsidian-tasks | ts
--- Issue
Postponing of tasks only works once
### Please check that this issue hasn't been reported before.

- [X] I searched previous [Bug Reports](https://github.com/obsidian-tasks-group/obsidian-tasks/issues?q=is%3Aopen+is%3Aissue+label%3A%22type%3A+bug%22+sort%3Acomments-desc) didn't find any similar reports.

### Expected Behavior

The new 'postpone' button should allow postponing a task multiple times.

### Current behaviour

After the first postponement of a task, any subsequent attempts to postpone it give an error.


### Steps to reproduce


1. In a vault with the development version of tasks build - such as version bb479979619d4ca745920dbd65906f39383837f9....
1. Create a file called, for example, `Postpone Tests.md`
2. Paste in this content:

~~~
## Task

- [ ] #task Do stuff 📅 2025-12-02

## Search

```tasks
path includes {{query.file.path}}
```
~~~

4. View in Live Preview or Reading mode

![image](https://github.com/obsidian-tasks-group/obsidian-tasks/assets/4840096/9bdf29fe-dd6e-45a3-a4b1-31507980a712)

5. Click on the Postpone icon at the end of the Task in the query results
6. **Check**:
    - the date should advance to 2025-12-03 in the markdown line
    - the date should advance to 2025-12-03 in the task in the tasks results
    - a success message should be displayed:
        ![image](https://github.com/obsidian-tasks-group/obsidian-tasks/assets/4840096/f2800ddf-d895-4233-940e-81a5fd9f03e2)

7. Click on the Postpone icon at the end of the
--- REAL FIX (ground truth)
diff --git a/src/Scripting/TasksDate.ts b/src/Scripting/TasksDate.ts
index fbb7ecc6..a9384c3f 100644
--- a/src/Scripting/TasksDate.ts
+++ b/src/Scripting/TasksDate.ts
@@ -122,6 +122,6 @@ export class TasksDate {
             return today.add(amount, unitOfTime);
         }
 
-        return this._date.add(amount, unitOfTime);
+        return this._date.clone().add(amount, unitOfTime);
     }
 }

--- Proposal A
## Root cause
The *Postpone* button updates the markdown line by editing the file at the task’s original line number.  
After the first postponement the task’s line number changes (the line is rewritten), but the
`Task` object that the button handler keeps using still holds the **old** line position.  
When the button is clicked a second time the plugin tries to edit the stale line, the edit
fails and the plugin shows an error notice.

## Fix
Update the UI after a successful postponement so that the task list (and the `Task` objects)
are rebuilt with the correct line numbers. The simplest place to do this is in the click‑
handler for the postpone button.

```diff
--- a/src/ui/TaskResultView.ts
+++ b/src/ui/TaskResultView.ts
@@
-    private async onPostponeClick(task: Task) {
-        try {
-            await this.taskEditor.postpone(task);
-            new Notice(t("postpone_success"));
-        } catch (e) {
-            console.error(e);
-            new Notice(t("postpone_error"));
-        }
-    }
+    /**
+     * Handler for the *Postpone* button shown next to a task in the query results.
+     *
+     * The original implementation edited the file but kept the stale `Task` instance,
+     * causing the second postponement to try to edit the wrong line and raise an error.
+     *
+     * After a successful postponement we now **refresh the query view**, which rebuilds
+     * all `Task` objects with up‑to‑date positions. This makes subsequent postponements work
+     * correctly.
+     */
+    private async onPostponeClick(task: Task) {
+        try {
+            await this.taskEditor.postpone(task);
+            new Notice(t("postpone_success"));
+
+            // Refresh the result view so that task positions are recomputed.
+            // This also updates the displayed date in the query results.
+            this.refresh();   // <-- added line
+        } catch (e) {
+            console.error(e);
+            new Notice(t("postpone_error"));
+        }
+    }
```

*Explanation of the change*  

* `this.refresh()` (already defined on `TaskResultView`) re‑runs the underlying
  `TasksQuery` and rebuilds the list of `Task` objects, thereby discarding the stale
  line information.  
* The rest of the logic (calling `taskEditor.postpone` and showing notices) stays the same.  
* No other files need to be touched.

## How to verify
1. Install the development build of the Tasks plugin containing the change.  
2. Open a vault, create a file `Postpone Tests.md` with the content from the issue.  
3. Open the file in Live Preview or Reading mode so the query block is rendered.  
4. Click the **Postpone** icon next to the task **once** – the date should change from
   `2025‑12‑02` to `2025‑12‑03` and a success notice appears.  
5. Click the **Postpone** icon **again** – the date should now change to `2025‑12‑04`,
   a success notice appears **once**, and **no error notice** is shown.  
6. Repeat the click several more times; each click should advance the date by one day
   without any error messages.

If the task line in the markdown file and the date shown in the query results both
advance correctly on every click, the bug is fixed.
--- Proposal B
## Root cause
The *Postpone* command rewrites the whole markdown line using the value stored in `task.originalLine`.  
After the first postponement the file is updated, but `originalLine` is **not** refreshed.  
When the command is invoked a second time it tries to replace the stale `originalLine` with a new line that already contains the updated date. The replace fails, the file is left unchanged and the plugin throws an error, resulting in the “failure” notice that appears after the success notice.

## Fix
Update the postpone implementation so it no longer relies on the stale `originalLine`.  
Instead of replacing the whole line we replace only the date token (the scheduled or due date) that is currently present in the file. After the edit we also update `task.originalLine` so subsequent actions work on the latest line.

```diff
--- a/src/Commands/PostponeCommand.ts
+++ b/src/Commands/PostponeCommand.ts
@@
 import { Task } from "../Task";
 import { moment } from "obsidian";
 import { TaskLocation } from "../TaskLocation";
 
 export class PostponeCommand extends Command {
   public static readonly commandName = "Postpone task";
@@
   async execute(task: Task, location: TaskLocation): Promise<void> {
-    // The original implementation replaced the whole line using the
-    // cached `originalLine`. After the first edit that cache becomes stale,
-    // causing the second edit to fail.
-
-    const newDate = task.getRelevantDate().add(1, "day");
-    const newLine = task.toFileLineWithDate(newDate);
-
-    await this.app.vault.modify(
-      location.file,
-      (content) => content.replace(task.originalLine, newLine),
-    );
-
-    // Update the cached line so that future commands see the latest state.
-    task.originalLine = newLine;
-
-    new Notice("Task postponed to " + newDate.format("YYYY-MM-DD"));
+    /* ------------------------------------------------------------------
+     * New implementation – replace only the date token.
+     *
+     * 1. Determine which date field (scheduled / due) is relevant for the
+     *    postponement.
+     * 2. Compute the new date (add one day).
+     * 3. Build a regular expression that matches the *exact* date string
+     *    currently present in the file (e.g. “📅 2025-12-02” or “⏳ 2025-12-02”).
+     * 4. Replace that date token with the new one.  This works regardless of
+     *    how many times the command has been run because it always looks at
+     *    the current content of the file, not at a stale cached line.
+     * 5. Refresh `task.originalLine` so other actions (e.g. further
+     *    postpones, completions) operate on the up‑to‑date line.
+     * ------------------------------------------------------------------ */
+
+    // 1️⃣ Determine which date we need to shift.
+    const dateInfo = task.getRelevantDateInfo(); // {type: "scheduled"|"due", date: Moment}
+    if (!dateInfo) {
+      new Notice("❌ No date to postpone on this task");
+      return;
+    }
+
+    // 2️⃣ Compute the new date.
+    const newDate = dateInfo.date.clone().add(1, "day");
+    const newDateString = newDate.format("YYYY-MM-DD");
+
+    // 3️⃣ Build a regex that matches the exact date token in the file.
+    //    The token includes the emoji that identifies the date type.
+    const emoji = dateInfo.type === "scheduled" ? "📅" : "⏳";
+    const dateRegex = new RegExp(`${emoji}\\s+${dateInfo.date.format("YYYY-MM-DD")}`);
+
+    // 4️⃣ Replace the token in the file content.
+    await this.app.va

=== TASK yannickcr__eslint-plugin-react-1527 | repo yannickcr/eslint-plugin-react | js
--- Issue
jsx-no-comment-textnodes rejecting html-entity "&#x2F;&#x2F;"
The rule `jsx-no-comment-textnodes` is warning when using html-entities for slashes
(`//` -> `&#x2F;&#x2F;`)

Example:
```html
<pre>&#x2F;&#x2F; TODO: Put instructions about usage here</pre>
```
This should not warn, since it is not valid comment.
--- REAL FIX (ground truth)
diff --git a/lib/rules/jsx-no-comment-textnodes.js b/lib/rules/jsx-no-comment-textnodes.js
index 87ec1937..1dc7b1b8 100644
--- a/lib/rules/jsx-no-comment-textnodes.js
+++ b/lib/rules/jsx-no-comment-textnodes.js
@@ -34,7 +34,10 @@ module.exports = {
 
     return {
       Literal: function(node) {
-        if (/^\s*\/(\/|\*)/m.test(node.value)) {
+        const sourceCode = context.getSourceCode();
+        // since babel-eslint has the wrong node.raw, we'll get the source text
+        const rawValue = sourceCode.getText(node);
+        if (/^\s*\/(\/|\*)/m.test(rawValue)) {
           // inside component, e.g. <div>literal</div>
           if (node.parent.type !== 'JSXAttribute' &&
               node.parent.type !== 'JSXExpressionContainer' &&

--- Proposal A
## Root cause
`jsx-no-comment-textnodes` checks JSX text nodes for comment‑like patterns (e.g. `//` or `/* … */`).  
The rule first **decodes HTML entities** before performing the check, so an entity such as `&#x2F;` (the slash character) is turned into `/`.  
Consequently a text node like  

```html
<pre>&#x2F;&#x2F; TODO …</pre>
```  

is decoded to `// TODO …` and the rule incorrectly reports it as a comment, even though the source contains the entity representation and is **not** a real comment.

## Fix
Skip the comment detection when the original source text contains an HTML entity that represents a slash. This keeps the rule’s original behaviour for real comments while preventing false positives for encoded slashes.

```diff
--- a/lib/rules/jsx-no-comment-textnodes.js
+++ b/lib/rules/jsx-no-comment-textnodes.js
@@
 module.exports = {
   meta: {
@@
   create(context) {
-    const sourceCode = context.getSourceCode();
-
-    /**
-     * Determines whether a given JSXText node looks like a comment.
-     * @param {ASTNode} node The JSXText node.
-     * @returns {boolean} True if it looks like a comment.
-     */
-    function isComment(node) {
-      // Decode HTML entities because JSXText values are already decoded.
-      const decoded = sourceCode.getText(node);
-
-      // Trim leading whitespace before checking for comment markers.
-      const trimmed = decoded.trimStart();
-
-      return (
-        trimmed.startsWith('//') ||
-        trimmed.startsWith('/*') ||
-        trimmed.startsWith('*')
-      );
-    }
-
-    return {
-      JSXText(node) {
-        if (isComment(node)) {
-          context.report({
-            node,
-            messageId: 'unexpected',
-          });
-        }
-      },
-    };
+    const sourceCode = context.getSourceCode();
+
+    /**
+     * Determines whether a given JSXText node looks like a comment.
+     * The rule decodes HTML entities before checking, but we must ignore
+     * cases where the original source contains an entity that represents a slash
+     * (e.g. &#x2F; or &#47;). Those are not real comments.
+     *
+     * @param {ASTNode} node The JSXText node.
+     * @returns {boolean} True if it looks like a comment.
+     */
+    function isComment(node) {
+      // The raw text as it appears in the source (including entities).
+      const raw = sourceCode.getText(node);
+
+      // If the raw source contains an HTML entity for a slash, we treat it
+      // as *not* a comment, because the author deliberately encoded the slash.
+      // This covers both hexadecimal (&#x2F;) and decimal (&#47;) forms.
+      if (/&#x2F;|&#47;/i.test(raw)) {
+        return false;
+      }
+
+      // Decode HTML entities – JSXText values are already decoded by the parser,
+      // but we keep this step for clarity and future‑proofing.
+      const decoded = raw;
+
+      // Trim leading whitespace before checking for comment markers.
+      const trimmed = decoded.trimStart();
+
+      return (
+        trimmed.startsWith('//') ||
+        trimmed.startsWith('/*') ||
+        trimmed.startsWith('*')
+      );
+    }
+
+    return {
+      JSXText(node) {
+        if (isComment(node)) {
+          context.report({
+            node,
+            messageId: 'unexpected',
+          });
+        }
+      },
+    };
   },
 };
```

### What changed
1. **Early exit for encoded slashes** – If the raw source contains `&#x2F;` or `&#47;`, the function returns `false` and the rule does not report.
2. A
--- Proposal B
## Root cause
`jsx-no-comment-textnodes` tries to detect comment‑like text nodes by **decoding HTML entities** before applying its comment‑detection RegExp.  
When the source contains the entity representation of a slash (`&#x2F;` or `&#47;`), the decoder turns it into “//”.  
The rule then thinks it has found a JavaScript comment and reports a warning, even though the original JSX text is **not** a comment (it is just the literal entity string).  

## Fix
1. **Do not decode HTML entities** before checking for comment markers.  
2. Keep the original detection logic but make it stricter:  
   * Anchor the RegExp to the start of a line (`^`) and use the multiline flag (`m`) so that only comment markers that start a line (optionally after whitespace) are flagged.  
   * Add an early‑exit when the raw JSX text contains an encoded slash (`&#x2F;` or `&#47;`). This prevents false positives for the common “//” entity representation.

### Unified diff
```diff
--- a/lib/rules/jsx-no-comment-textnodes.js
+++ b/lib/rules/jsx-no-comment-textnodes.js
@@
-const entities = require('entities'); // used to decode HTML entities
-
-/** ---------------------------------------------------------------------------
- * Rule Definition
- * --------------------------------------------------------------------------- */
-
-module.exports = {
-  meta: {
-    docs: {
-      description: 'Disallow comment text nodes in JSX',
-      category: 'Possible Errors',
-      recommended: false,
-    },
-    schema: [], // no options
-  },
-
-  create(context) {
-    return {
-      JSXText(node) {
-        // Decode HTML entities so that `&#x2F;&#x2F;` becomes `//`
-        const decoded = entities.decodeHTML(node.value);
-
-        // Detect comment markers (//, /*, */) anywhere in the text
-        if (/\\/(\\/|\\*)/.test(decoded)) {
-          context.report({
-            node,
-            message: 'Comment text nodes are not allowed in JSX.',
-          });
-        }
-      },
-    };
-  },
-};
+/** ---------------------------------------------------------------------------
+ * Rule Definition
+ * --------------------------------------------------------------------------- */
+
+module.exports = {
+  meta: {
+    docs: {
+      description: 'Disallow comment text nodes in JSX',
+      category: 'Possible Errors',
+      recommended: false,
+    },
+    schema: [], // no options
+  },
+
+  create(context) {
+    // RegExp that matches a comment marker only when it appears at the start of
+    // a line (ignoring leading whitespace). The `m` flag makes `^` work per line.
+    const COMMENT_REGEX = /^\\s*\\/(\\/|\\*)/m;
+
+    // HTML entity for a forward slash – both hex and decimal forms.
+    const ENCODED_SLASH_REGEX = /&#x?2F;/i;
+
+    return {
+      JSXText(node) {
+        const raw = node.value;\n+\n+        // If the raw text contains an encoded slash, we assume the author is\n+        // intentionally using an HTML entity (e.g. `&#x2F;&#x2F;`) and we should\n+        // not treat it as a comment.\n+        if (ENCODED_SLASH_REGEX.test(raw)) {\n+          return;\n+        }\n+\n+        // Only flag comment markers that start a line (optionally after spaces).\n+        // This mirrors the behaviour of browsers where `//` inside a string is\n+        // not a comment, and also prevents false positives on URLs.\n+        if (COMMENT_REGEX.test(raw)) {\n+          context.report({\n+            node,\n+            message: 'Comment text nodes are not allowed in J

=== TASK alibaba__fescar-619 | repo alibaba/fescar | java
--- Issue
change version to 0.4.0-SNAPSHOT
- [ ] I have searched the [issues](https://github.com/alibaba/fescar/issues) of this repository and believe that this is not a duplicate.

### Ⅰ. Issue Description

change version to 0.4.0-SNAPSHOT

### Ⅱ. Describe what happened

  If there is an exception, please attach the exception trace:

```
Just paste your stack trace here!
```


### Ⅲ. Describe what you expected to happen


### Ⅳ. How to reproduce it (as minimally and precisely as possible)

1. xxx
2. xxx
3. xxx

### Ⅴ. Anything else we need to know?


### Ⅵ. Environment:

- JDK version :
- OS :
- Others:
--- REAL FIX (ground truth)
diff --git a/common/pom.xml b/common/pom.xml
index 5dabc7270..ea99b1111 100644
--- a/common/pom.xml
+++ b/common/pom.xml
@@ -20,7 +20,7 @@
     <parent>
         <artifactId>fescar-all</artifactId>
         <groupId>com.alibaba.fescar</groupId>
-        <version>0.3.1</version>
+        <version>0.4.0-SNAPSHOT</version>
     </parent>
     <modelVersion>4.0.0</modelVersion>
     <artifactId>fescar-common</artifactId>
diff --git a/common/src/main/java/com/alibaba/fescar/common/exception/NotSupportYetException.java b/common/src/main/java/com/alibaba/fescar/common/exception/NotSupportYetException.java
index 402d522ed..3abbacd42 100644
--- a/common/src/main/java/com/alibaba/fescar/common/exception/NotSupportYetException.java
+++ b/common/src/main/java/com/alibaba/fescar/common/exception/NotSupportYetException.java
@@ -18,6 +18,8 @@ package com.alibaba.fescar.common.exception;
 
 /**
  * The type Not support yet exception.
+ *
+ * @author jimin.jm @alibaba-inc.com
  */
 public class NotSupportYetException extends RuntimeException {
 
diff --git a/common/src/main/java/com/alibaba/fescar/common/exception/ShouldNeverHappenException.java b/common/src/main/java/com/alibaba/fescar/common/exception/ShouldNeverHappenException.java
index 6044c8a9f..c6080c4ff 100644
--- a/common/src/main/java/com/alibaba/fescar/common/exception/ShouldNeverHappenException.java
+++ b/common/src/main/java/com/alibaba/fescar/common/exception/ShouldNeverHappenException.java
@@ -18,6 +18,8 @@ package com.alibaba.fescar.common.exception;
 
 /**
  * The type Should never happen exception.
+ *
+ * @author jimin.jm @alibaba-inc.com
  */
 public class ShouldNeverHappenException extends RuntimeException {
 
diff --git a/common/src/main/java/com/alibaba/fescar/common/thread/RejectedPolicies.java b/common/src/main/java/com/alibaba/fescar/common/thread/RejectedPolicies.java
index 32f661642..d1060bfbd 100644
--- a/common/src/main/java/com/alibaba/fescar/common/thread/RejectedPolicies.java
+++ b/common/src/main/java/com/alibaba/fescar/common/thread/RejectedPolicies.java
@@ -23,7 +23,7 @@ import java.util.concurrent.ThreadPoolExecutor;
 /**
  * Policies for RejectedExecutionHandler
  *
- * Created by guoyao on 2019/2/26.
+ * @author guoyao
  */
 public final class RejectedPolicies {
 
diff --git a/common/src/main/java/com/alibaba/fescar/common/util/BlobUtils.java b/common/src/main/java/com/alibaba/fescar/common/util/BlobUtils.java
index a1cc3bd24..9954a14c7 100644
--- a/common/src/main/java/com/alibaba/fescar/common/util/BlobUtils.java
+++ b/common/src/main/java/com/alibaba/fescar/common/util/BlobUtils.java
@@ -26,6 +26,8 @@ import com.alibaba.fescar.common.exception.ShouldNeverHappenException;
 
 /**
  * The type Blob utils.
+ *
+ * @author jimin.jm @alibaba-inc.com
  */
 public class BlobUtils {
 
diff --git a/common/src/main/java/com/alibaba/fescar/common/util/StringUtils.java b/common/src/main/java/com/alibaba/fescar/common/util/StringUtils.java
index f4f20e556..94a771031 100644
--- a/common
--- Proposal A
## Root cause
The project’s Maven coordinates and the hard‑coded version string in the source code still point to the previous release (e.g., `0.3.x`).  When a new development cycle starts the version must be bumped to `0.4.0‑SNAPSHOT` in:

1. The root `pom.xml` (the main project version).  
2. All child module `pom.xml` files that inherit the parent version via the `<parent>` section.  
3. The `Version` utility class that exposes the library version at runtime (the constant should reflect the **release** version without the `-SNAPSHOT` suffix).

Because these places were not updated, the built artifacts report the old version and any downstream projects that depend on the snapshot will resolve the wrong version.

---

## Fix
```diff
--- a/pom.xml
+++ b/pom.xml
@@
-    <version>0.3.0</version>
+    <version>0.4.0-SNAPSHOT</version>
@@
-    <properties>
-        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
-        <java.version>1.8</java.version>
-    </properties>
+    <properties>
+        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
+        <java.version>1.8</java.version>
+        <!-- other existing properties -->
+    </properties>
```

```diff
--- a/fescar-common/pom.xml
+++ b/fescar-common/pom.xml
@@
-    <parent>
-        <groupId>com.alibaba</groupId>
-        <artifactId>fescar</artifactId>
-        <version>0.3.0</version>
-        <relativePath>../pom.xml</relativePath>
-    </parent>
+    <parent>
+        <groupId>com.alibaba</groupId>
+        <artifactId>fescar</artifactId>
+        <version>0.4.0-SNAPSHOT</version>
+        <relativePath>../pom.xml</relativePath>
+    </parent>
```

*(The same `<parent>` version change must be applied to every module `pom.xml` – e.g., `fescar-core/pom.xml`, `fescar-spring/pom.xml`, `fescar-server/pom.xml`, etc. For brevity only one module is shown.)*

```diff
--- a/fescar-common/src/main/java/com/alibaba/fescar/common/Version.java
+++ b/fescar-common/src/main/java/com/alibaba/fescar/common/Version.java
@@
-    public static final String VERSION = "0.3.0";
+    /**
+     * The current released version of Fescar.
+     * <p>
+     * NOTE: This constant reflects the **release** version (without the
+     * {@code -SNAPSHOT} suffix).  The Maven project version is managed
+     * separately in the POM files.
+     */
+    public static final String VERSION = "0.4.0";
```

### Summary of changes
| File | Change |
|------|--------|
| `pom.xml` (root) | Bumped `<version>` to `0.4.0-SNAPSHOT`. |
| All module `pom.xml` files | Updated `<parent>` version to `0.4.0-SNAPSHOT`. |
| `Version.java` | Updated the public `VERSION` constant to `0.4.0` (release version, no `-SNAPSHOT`). |

---

## How to verify
1. **Build the project**  
   ```bash
   mvn clean install -DskipTests
   ```  
   The build should succeed without version‑conflict warnings.

2. **Check the generated artifacts**  
   After the build, inspect any produced JAR’s `META-INF/MANIFEST.MF` (or run `java -jar ... --version` if the project provides a CLI) and confirm that the version reported is `0.4.0`.

3. **Run the test suite**  
   ```bash
   mvn test
   ```  
   All tests must pass, confirming that the version bump did not break any functionality.

4. **Verify the `Version` class**  
   Execute a small snippet (or a unit test) that prints `Version.VERSION` and ensure it outputs `0.4.0`.

5. **Dependency resolution**  
   In a separate Maven project, add a dependency on the newly built sna
--- Proposal B
## Root cause
The project’s Maven `pom.xml` (and possibly module POMs) still declare the old release version (e.g., `0.3.x`). This causes the built artifacts to be published with the wrong version number. The issue requests a version bump to `0.4.0‑SNAPSHOT`, but the POM files were never updated.

## Fix
```diff
--- a/pom.xml
+++ b/pom.xml
@@
-    <groupId>io.seata</groupId>
-    <artifactId>fescar</artifactId>
-    <version>0.3.0</version>
+    <groupId>io.seata</groupId>
+    <artifactId>fescar</artifactId>
+    <version>0.4.0-SNAPSHOT</version>
@@
-    <parent>
-        <groupId>io.seata</groupId>
-        <artifactId>fescar-parent</artifactId>
-        <version>0.3.0</version>
-    </parent>
+    <parent>
+        <groupId>io.seata</groupId>
+        <artifactId>fescar-parent</artifactId>
+        <version>0.4.0-SNAPSHOT</version>
+    </parent>
```

If the project contains additional module POMs (e.g., `fescar-core/pom.xml`, `fescar-spring/pom.xml`, etc.), apply the same version change there:

```diff
--- a/fescar-core/pom.xml
+++ b/fescar-core/pom.xml
@@
-    <version>0.3.0</version>
+    <version>0.4.0-SNAPSHOT</version>
```

(Repeat for each module POM.)

Optionally, update the version reference in `README.md` or any documentation files:

```diff
--- a/README.md
+++ b/README.md
@@
-Current version: **0.3.0**
+Current version: **0.4.0‑SNAPSHOT**
```

## How to verify
1. **Build the project**  
   ```bash
   mvn clean install -DskipTests
   ```
   Ensure the build succeeds without version‑related errors.

2. **Check the effective version**  
   ```bash
   mvn help:evaluate -Dexpression=project.version -q -DforceStdout
   ```
   The output should be:
   ```
   0.4.0-SNAPSHOT
   ```

3. **Inspect generated artifacts**  
   Verify that the JAR filenames in `target/` contain `0.4.0-SNAPSHOT`, e.g., `fescar-0.4.0-SNAPSHOT.jar`.

4. **Optional – CI verification**  
   Push the change to a branch and let the repository’s CI pipeline run. All version‑related checks (if any) should now pass.

=== TASK python-trio__trio-1129 | repo python-trio/trio | python
--- Issue
Should we somehow expose Pipe*Stream on Unix, and if so, how?
We have `PipeReceiveStream` and `PipeSendStream` classes on Unix, that are currently not exposed publicly; they're only used internally for subprocesses.

The actual code is a pretty generic building block: they can wrap around any Unix file-descriptor that supports non-blocking mode. So they could potentially be useful for talking to [FIFOs](http://man7.org/linux/man-pages/man7/fifo.7.html), TTYs, stdio streams (#174), etc.

Should we expose them more directly? If so, how? In `trio.hazmat`, with some sort of [`os.fdopen`](https://docs.python.org/3/library/os.html#os.fdopen) equivalent?

If we do we'll want to think through the ownership semantics: should they take ownership of a passed in fd, or should they dup it?

Also, they should restore the fd's blocking/non-blocking state before closing it.
--- REAL FIX (ground truth)
diff --git a/docs-requirements.txt b/docs-requirements.txt
index 02ecb74a..d1466815 100644
--- a/docs-requirements.txt
+++ b/docs-requirements.txt
@@ -11,7 +11,7 @@ babel==2.7.0              # via sphinx
 certifi==2019.6.16        # via requests
 chardet==3.0.4            # via requests
 click==7.0                # via towncrier
-docutils==0.14            # via sphinx
+docutils==0.15            # via sphinx
 idna==2.8
 imagesize==1.1.0          # via sphinx
 immutables==0.9
@@ -21,7 +21,7 @@ markupsafe==1.1.1         # via jinja2
 outcome==1.0.0
 packaging==19.0           # via sphinx
 pygments==2.4.2           # via sphinx
-pyparsing==2.4.0          # via packaging
+pyparsing==2.4.1          # via packaging
 pytz==2019.1              # via babel
 requests==2.22.0          # via sphinx
 six==1.12.0               # via packaging
diff --git a/docs/source/reference-hazmat.rst b/docs/source/reference-hazmat.rst
index 6cad8476..e12d3601 100644
--- a/docs/source/reference-hazmat.rst
+++ b/docs/source/reference-hazmat.rst
@@ -174,6 +174,26 @@ All environments provide the following functions:
    yourself afterwards.
 
 
+Unix-specific API
+-----------------
+
+`FdStream` supports wrapping Unix files (such as a pipe or TTY) as
+a stream.
+
+If you have two different file descriptors for sending and receiving,
+and want to bundle them together into a single bidirectional
+`~trio.abc.Stream`, then use `trio.StapledStream`::
+
+    bidirectional_stream = trio.StapledStream(
+        trio.hazmat.FdStream(write_fd),
+        trio.hazmat.FdStream(read_fd)
+    )
+
+.. autoclass:: FdStream
+   :show-inheritance:
+   :members:
+
+
 Kqueue-specific API
 -------------------
 
diff --git a/newsfragments/829.feature.rst b/newsfragments/829.feature.rst
new file mode 100644
index 00000000..34090857
--- /dev/null
+++ b/newsfragments/829.feature.rst
@@ -0,0 +1,1 @@
+Add `trio.hazmat.FdStream` for wrapping a Unix file descriptor as a `~trio.abc.Stream`.
diff --git a/trio/_subprocess.py b/trio/_subprocess.py
index ae530e3a..f9bc0647 100644
--- a/trio/_subprocess.py
+++ b/trio/_subprocess.py
@@ -1,9 +1,8 @@
 import os
-import select
 import subprocess
-from functools import partial
+from typing import Optional
 
-from ._abc import AsyncResource
+from ._abc import AsyncResource, SendStream, ReceiveStream
 from ._highlevel_generic import StapledStream
 from ._sync import Lock
 from ._subprocess_platform import (
@@ -101,9 +100,10 @@ class Process(AsyncResource):
                     .format(key)
                 )
 
-        self.stdin = None
-        self.stdout = None
-        self.stderr = None
+        self.stdin = None  # type: Optional[SendStream]
+        self.stdout = None  # type: Optional[ReceiveStream]
+        self.stderr = None  # type: Optional[ReceiveStream]
+        self.stdio = None  # type: Optional[StapledStream]
 
         if os.name == "posix":
             if isinstance(command, str) and not options.get("shell"):
@@ -153,8 +153,6 @@ class Process(AsyncR
--- Proposal A
## Root cause
`PipeReceiveStream` and `PipeSendStream` are internal helpers used by Trio’s subprocess implementation.  
They are deliberately **not** part of the public API, so they are not exported from
`trio.hazmat`.  Because they are fully‑featured asynchronous stream wrappers for any
Unix file descriptor, they are useful for users who want to work with FIFOs,
TTYs, stdio, etc.  The lack of a public entry point (and the missing ownership
semantics) makes it impossible to use them safely.

## Fix
* Export the two classes from the public “hazmat” namespace.
* Add an optional `closefd` argument (default `True`) to the constructors.
  * When `closefd=False` the constructor duplicates the supplied fd (`os.dup`)
    and takes ownership of the duplicate, leaving the original fd untouched.
  * When `closefd=True` the original fd is taken ownership of (the current
    behaviour).
* Update the doc‑strings to describe the ownership model and the restoration of
  the original blocking mode on close.
* Add the classes to `__all__` so they appear in the public API.

### Unified diff

```diff
--- a/trio/_core/_subprocess.py
+++ b/trio/_core/_subprocess.py
@@
-class PipeReceiveStream(_BasePipeStream):
-    """Asynchronous read‑only stream that wraps a Unix file descriptor.
-
-    This class is used internally by Trio’s subprocess support.
-    """
-
-    def __init__(self, fd):
-        super().__init__(fd, readable=True, writable=False)
+class PipeReceiveStream(_BasePipeStream):
+    """Asynchronous read‑only stream that wraps a Unix file descriptor.
+
+    This class is used internally by Trio’s subprocess support, but it is also
+    useful for any non‑blocking Unix file descriptor (e.g. FIFOs, TTYs,
+    ``stdin``/``stdout``).  It can be imported from :mod:`trio.hazmat`.
+
+    Parameters
+    ----------
+    fd: int
+        The file descriptor to wrap.
+    closefd: bool, optional
+        If *True* (the default) the stream takes ownership of *fd* and will
+        close it when the stream is closed.  If *False* the stream duplicates
+        *fd* (using :func:`os.dup`) and takes ownership of the duplicate,
+        leaving the original descriptor untouched.
+    """
+
+    def __init__(self, fd, *, closefd: bool = True):
+        # If we are not supposed to close the original fd, duplicate it.
+        if not closefd:
+            fd = os.dup(fd)
+        super().__init__(fd, readable=True, writable=False)
@@
-class PipeSendStream(_BasePipeStream):
-    """Asynchronous write‑only stream that wraps a Unix file descriptor.
-
-    This class is used internally by Trio’s subprocess support.
-    """
-
-    def __init__(self, fd):
-        super().__init__(fd, readable=False, writable=True)
+class PipeSendStream(_BasePipeStream):
+    """Asynchronous write‑only stream that wraps a Unix file descriptor.
+
+    This class is used internally by Trio’s subprocess support, but it can be
+    used directly for any non‑blocking Unix file descriptor (e.g. FIFOs,
+    TTYs, ``stdin``/``stdout``).  It can be imported from :mod:`trio.hazmat`.
+
+    Parameters
+    ----------
+    fd: int
+        The file descriptor to wrap.
+    closefd: bool, optional
+        If *True* (the default) the stream takes ownership of *fd* and will
+        close it when the stream is closed.  If *False* the stream duplicates
+        *fd* (using :func:`os.dup`) and takes ownership of the duplicate,
+        leaving the original descriptor untouched.
+    """
+
+    def __init
--- Proposal B
## Root cause
`PipeReceiveStream` and `PipeSendStream` are defined in the Trio code‑base (in `trio/_subprocess.py`) but they are not exported from any public module.  Consequently users cannot import them or use them as a generic “wrap an arbitrary Unix file descriptor” primitive, even though the implementation already supports non‑blocking I/O, restores the original blocking mode on close, and works with any FD that behaves like a pipe (FIFOs, TTYs, etc.).  The issue is therefore simply that the classes (and a convenient factory) are hidden from the public API.

## Fix
* Export the two classes from a public namespace (`trio.lowlevel` – the place for low‑level building blocks).
* Provide a small factory helper `open_pipe(fd, *, mode, closefd=True)` that:
  * validates `mode` (`"r"`/`"rb"` → `PipeReceiveStream`, `"w"`/`"wb"` → `PipeSendStream`);
  * optionally duplicates the FD when `closefd=False` (so the original FD is not closed when the stream is closed);
  * returns the appropriate stream instance.
* Add the new symbols to `__all__` so they appear in the generated documentation and are importable via `from trio.lowlevel import …`.

Below is a **unified diff** that implements the above changes.  The diff assumes the existing implementation of `PipeReceiveStream` and `PipeSendStream` lives in `trio/_subprocess.py`.

```diff
--- a/trio/_subprocess.py
+++ b/trio/_subprocess.py
@@
 from ._core._run import run_sync_soon
 from ._core._tasks import current_task
 from ._core._traps import wait_readable, wait_writable
 from ._core._util import aclose_forcefully, aclose_gracefully
 from ._core._windows import is_windows
@@
 __all__ = [
     "Subprocess",
     "SubprocessError",
     "SubprocessProtocol",
-    # Pipe*Stream are deliberately *not* exported – they are internal.
+    # Pipe*Stream are now part of the public low‑level API.
+    "PipeReceiveStream",
+    "PipeSendStream",
+    "open_pipe",
 ]
 
@@
 class PipeReceiveStream(AsyncResource, ReceiveStream):
@@
 class PipeSendStream(AsyncResource, SendStream):
@@
     async def aclose(self) -> None:
         await aclose_gracefully(self._fd, self._original_blocking)
 
+# ----------------------------------------------------------------------
+# Public helper – expose a generic “wrap an fd as a Trio stream”.
+# ----------------------------------------------------------------------
+
+def _dup_fd(fd: int) -> int:
+    """Duplicate *fd* using ``os.dup`` and set the duplicate to non‑blocking.
+
+    The caller is responsible for closing the returned descriptor.
+    """
+    import os
+
+    dup = os.dup(fd)
+    # Ensure the duplicate is in non‑blocking mode – the stream classes
+    # expect this and will restore the original mode on close.
+    flags = os.fcntl(dup, os.F_GETFL)
+    os.fcntl(dup, os.F_SETFL, flags | os.O_NONBLOCK)
+    return dup
+
+
+def open_pipe(fd: int, *, mode: str, closefd: bool = True) -> AsyncResource:
+    """Wrap an existing Unix file descriptor in a Trio stream.
+
+    Parameters
+    ----------
+    fd:
+        The file descriptor to wrap.  It must refer to an object that
+        supports non‑blocking I/O (e.g. a pipe, FIFO, TTY, socket, etc.).
+    mode:
+        ``"r"`` or ``"rb"`` for a receive‑only stream,
+        ``"w"`` or ``"wb"`` for a send‑only stream.
+    closefd:
+        If ``True`` (the default) the returned stream takes ownership of
+        *fd* and will close it when the stream is closed.  If ``False`` the
+        descriptor is duplicated (u

=== TASK pion__turn-243 | repo pion/turn | go
--- Issue
Error when handling datagram
### Your environment.
Windows Server 2019
Pion Turn server 2.0.2

### What did you do?
I executed "turn-server-simple.exe" to create a Turn server for a WebRTC project Im developing

### What did you expect?
To work ok. It does sometimes. Others it get an error.

### What happened?
When haviong a videocall using WebRTC, the console throws a lots of errors. For example:

turn ERROR: 2021/03/09 09:47:34 error when handling datagram: failed to handle Refresh-request from 94.73.44.131:30866: no allocation found for 94.73.44.131:30866:0.0.0.0:3478
turn ERROR: 2021/03/09 09:47:34 error when handling datagram: failed to handle Refresh-request from 94.73.44.131:30858: no allocation found for 94.73.44.131:30858:0.0.0.0:3478
turn ERROR: 2021/03/09 09:47:34 error when handling datagram: failed to handle Refresh-request from 94.73.44.131:30860: no allocation found for 94.73.44.131:30860:0.0.0.0:3478
turn ERROR: 2021/03/09 09:52:12 error when handling datagram: failed to create stun message from packet: unexpected EOF: not enough bytes to read header
turn ERROR: 2021/03/09 09:52:12 error when handling datagram: failed to create stun message from packet: unexpected EOF: not enough bytes to read header
turn ERROR: 2021/03/09 09:52:25 error when handling datagram: failed to create stun message from packet: unexpected EOF: not enough bytes to read header

Others, I get:

turn ERROR: 2021/03/09 12:44:52 error when handling datagram: failed to 
--- REAL FIX (ground truth)
diff --git a/AUTHORS.txt b/AUTHORS.txt
index 8515b5c..ed79eeb 100644
--- a/AUTHORS.txt
+++ b/AUTHORS.txt
@@ -29,6 +29,7 @@ Sean DuBois <seaduboi@amazon.com>
 Sean DuBois <sean@pion.ly>
 Sean DuBois <sean@siobud.com>
 songjiayang <songjiayang@jd.com>
+ted <ted@skydio.com>
 Tom Clift <tom@clift.id.au>
 Yusuke Nakamura <yusuke1994525@gmail.com>
 Yutaka Takeda <yt0916@gmail.com>
diff --git a/internal/allocation/allocation.go b/internal/allocation/allocation.go
index 2b346f1..ac696ec 100644
--- a/internal/allocation/allocation.go
+++ b/internal/allocation/allocation.go
@@ -4,6 +4,7 @@ package allocation
 import (
 	"net"
 	"sync"
+	"sync/atomic"
 	"time"
 
 	"github.com/pion/logging"
@@ -12,6 +13,11 @@ import (
 	"github.com/pion/turn/v2/internal/proto"
 )
 
+type allocationResponse struct {
+	transactionID [stun.TransactionIDSize]byte
+	responseAttrs []stun.Setter
+}
+
 // Allocation is tied to a FiveTuple and relays traffic
 // use CreateAllocation and GetAllocation to operate
 type Allocation struct {
@@ -27,6 +33,12 @@ type Allocation struct {
 	lifetimeTimer       *time.Timer
 	closed              chan interface{}
 	log                 logging.LeveledLogger
+
+	// some clients (firefox or others using resiprocate's nICE lib) may retry allocation
+	// with same 5 tuple when received 413, for compatible with these clients,
+	// cache for response lost and client retry to implement 'stateless stack approach'
+	// https://datatracker.ietf.org/doc/html/rfc5766#section-6.2
+	responseCache atomic.Value // *allocationResponse
 }
 
 func addr2IPFingerprint(addr net.Addr) string {
@@ -164,6 +176,23 @@ func (a *Allocation) Refresh(lifetime time.Duration) {
 	}
 }
 
+// SetResponseCache cache allocation response for retransmit allocation request
+func (a *Allocation) SetResponseCache(transactionID [stun.TransactionIDSize]byte, attrs []stun.Setter) {
+	a.responseCache.Store(&allocationResponse{
+		transactionID: transactionID,
+		responseAttrs: attrs,
+	})
+}
+
+// GetResponseCache return response cache for retransmit allocation request
+func (a *Allocation) GetResponseCache() (id [stun.TransactionIDSize]byte, attrs []stun.Setter) {
+	if r := a.responseCache.Load(); r != nil {
+		res := r.(*allocationResponse)
+		id, attrs = res.transactionID, res.responseAttrs
+	}
+	return
+}
+
 // Close closes the allocation
 func (a *Allocation) Close() error {
 	select {
diff --git a/internal/server/turn.go b/internal/server/turn.go
index cbac09c..ca92f8d 100644
--- a/internal/server/turn.go
+++ b/internal/server/turn.go
@@ -39,8 +39,14 @@ func handleAllocateRequest(r Request, m *stun.Message) error {
 	//    existing allocation.  If yes, the server rejects the request with
 	//    a 437 (Allocation Mismatch) error.
 	if alloc := r.AllocationManager.GetAllocation(fiveTuple); alloc != nil {
-		msg := buildMsg(m.TransactionID, stun.NewType(stun.MethodAllocate, stun.ClassErrorResponse), &stun.ErrorCodeAttribute{Code: stun.CodeAllocMismatch})
-		return buildAndSendErr(r.Conn,
--- Proposal A
## Root cause
The TURN server kept allocations alive even when a client sent a **REFRESH** request with a *zero* lifetime (or when the allocation naturally expired).  
In the TURN RFC a zero‑lifetime REFRESH is a *de‑allocation* request.  
Because the server ignored this case it never removed the allocation from the `AllocationManager`.  

Consequences  

* Subsequent **Allocate** requests from the same 5‑tuple hit the “relay already allocated for 5‑TUPLE” error.  
* Later **Refresh** requests for the same client could not find the allocation and produced the “no allocation found …” error.  
* Stale allocations also caused the server to read leftover bytes from the previous packet, leading to the “unexpected EOF: not enough bytes to read header” parsing errors.

The fix is to explicitly delete the allocation when a REFRESH request carries a zero lifetime and to make sure the allocation manager is created before allocating the read buffer (preventing a nil‑manager panic that could also corrupt the read path).

---

## Fix
```diff
--- a/turn/server.go
+++ b/turn/server.go
@@
 func (s *Server) Run() error {
@@
-    // Create a manager for allocations.
-    allocationMgr, err := allocation.NewManager(s.config.AllocationLifetime, s.logger)
-    if err != nil {
-        return err
-    }
-
-    // Buffer used for reading inbound packets.
-    buf := make([]byte, inboundMTU)
-
-    // Start read loops for each listener.
-    for _, l := range s.listeners {
-        go s.connReadLoop(l, allocationMgr, buf)
-    }
+    // Create a manager for allocations **before** allocating the read buffer.
+    // This guarantees that we never allocate a buffer when the manager could not be created,
+    // avoiding a situation where the read loops run with a nil manager.
+    allocationMgr, err := allocation.NewManager(s.config.AllocationLifetime, s.logger)
+    if err != nil {
+        return err
+    }
+
+    // Buffer used for reading inbound packets.
+    buf := make([]byte, inboundMTU)
+
+    // Start read loops for each listener.
+    for _, l := range s.listeners {
+        go s.connReadLoop(l, allocationMgr, buf)
+    }
@@
-func (s *Server) handleRefreshRequest(req *stun.Message, src net.Addr, a *allocation.Allocation) error {
-    // Parse the REFRESH request.
-    var refresh stun.Refresh
-    if err := refresh.GetFrom(req); err != nil {
-        return err
-    }
-
-    // Update the allocation lifetime.
-    a.SetLifetime(refresh.Lifetime)
-    return nil
-}
+func (s *Server) handleRefreshRequest(req *stun.Message, src net.Addr, a *allocation.Allocation) error {
+    // Parse the REFRESH request.
+    var refresh stun.Refresh
+    if err := refresh.GetFrom(req); err != nil {
+        return err
+    }
+
+    // RFC 5766 § 6.3 – a zero‑lifetime REFRESH is a de‑allocation request.
+    // In that case we must delete the allocation and respond with a success.
+    if refresh.Lifetime == 0 {
+        // Delete the allocation from the manager.
+        if err := s.allocationMgr.Delete(a); err != nil {
+            // Log but do not treat as fatal – we still need to send a response.
+            s.logger.Warnf("failed to delete allocation on zero‑lifetime REFRESH: %v", err)
+        }
+        // No further processing required; the caller will build a success response.
+        return nil
+    }
+
+    // Normal refresh – extend the lifetime.
+    a.SetLifetime(refresh.Lifetime)
+    return nil
+}
```

**Explanation of changes**

1. **Allocation deleti
--- Proposal B
## Root cause
The TURN server parses every incoming UDP packet directly from the slice that is reused by the listener’s read loop.  
When many packets arrive concurrently the same underlying buffer is handed to several goroutines.  
While one goroutine is still decoding a STUN message, another read overwrites the buffer.  
This results in:

* **`unexpected EOF: not enough bytes to read header`** – the STUN decoder sees a truncated packet because the buffer was overwritten.
* **`no allocation found …`** – the allocation lookup is performed with a corrupted client address (the source port may have been overwritten), so the server cannot find the previously created allocation.
* **`relay already allocated for 5‑TUPLE`** – the corrupted packet is interpreted as a new Allocate request, causing the server to think a second allocation for the same 5‑tuple is being created.

The bug is a classic data‑race caused by re‑using the read buffer across goroutines.

## Fix
* Allocate a fresh copy of the received packet before handing it to any worker goroutine.
* Keep the client address (`net.UDPAddr`) immutable for the lifetime of the request.
* Update the packet‑handling code to use the copied buffer, eliminating the race.

```diff
--- a/turn/server.go
+++ b/turn/server.go
@@
 func (s *Server) serveUDP(conn *net.UDPConn) {
-    buf := make([]byte, maxPacketSize)
-    for {
-        n, addr, err := conn.ReadFromUDP(buf)
-        if err != nil {
-            s.logger.Errorf("read udp error: %v", err)
-            continue
-        }
-
-        // Hand the packet to a worker goroutine.
-        go s.handleUDPDatagram(conn, buf[:n], addr)
-    }
+    // NOTE: The buffer used for ReadFromUDP must not be shared between
+    // concurrent goroutines.  Each incoming packet gets its own copy so
+    // that the decoder cannot see data that is overwritten by a later
+    // read.
+    sharedBuf := make([]byte, maxPacketSize)
+    for {
+        n, addr, err := conn.ReadFromUDP(sharedBuf)
+        if err != nil {
+            s.logger.Errorf("read udp error: %v", err)
+            continue
+        }
+
+        // Copy the exact payload that was read.  This copy is owned by the
+        // goroutine that will process the packet, preventing data races.
+        pktCopy := make([]byte, n)
+        copy(pktCopy, sharedBuf[:n])
+
+        // Preserve the client address – net.UDPAddr is a struct containing a
+        // slice for IP, which must also be copied to avoid later mutation.
+        clientAddr := &net.UDPAddr{
+            IP:   append([]byte(nil), addr.IP...), // deep copy of IP slice
+            Port: addr.Port,
+            Zone: addr.Zone,
+        }
+
+        // Hand the packet to a worker goroutine.
+        go s.handleUDPDatagram(conn, pktCopy, clientAddr)
+    }
 }
 
 func (s *Server) handleUDPDatagram(conn *net.UDPConn, pkt []byte, src *net.UDPAddr) {
@@
-    msg, err := stun.NewMessage(pkt)
-    if err != nil {
-        s.logger.Errorf("failed to create stun message from packet: %v", err)
-        return
-    }
+    // Decode the STUN message.  Any parsing error is now safe – the packet
+    // cannot be corrupted by another concurrent read.
+    msg, err := stun.NewMessage(pkt)
+    if err != nil {
+        s.logger.Errorf("failed to create stun message from packet: %v", err)
+        return
+    }
```

### What was changed?
1. **Buffer handling** – a single reusable buffer (`sharedBuf`) is still used for the `ReadFromUDP` call, but a *new* s

=== TASK julialinearalgebra__genericlinearalgebra.jl-125 | repo JuliaLinearAlgebra/GenericLinearAlgebra.jl | julia
--- Issue
svdvals! failing for empty matrices
The singular value decompositon fails for empty matrices with element types not `<: BlasFloat`:

```julia
julia> svdvals!(Float16.(ones(10,0)))
ERROR: DimensionMismatch("length of diagonal vector is 0, length of off-diagonal vector is 0")
Stacktrace:
 [1] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:11 [inlined]
 [2] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:17 [inlined]
 [3] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:75 [inlined]
 [4] bidiagonalize!(A::Matrix{Float16})
   @ GenericLinearAlgebra ~/.julia/packages/GenericLinearAlgebra/JoKjw/src/svd.jl:269
 [5] svdvals!(A::Matrix{Float16}; tol::Float16, debug::Bool)
   @ GenericLinearAlgebra ~/.julia/packages/GenericLinearAlgebra/JoKjw/src/svd.jl:487
 [6] svdvals!(A::Matrix{Float16})
   @ GenericLinearAlgebra ~/.julia/packages/GenericLinearAlgebra/JoKjw/src/svd.jl:487
 [7] top-level scope
   @ REPL[121]:1

julia> svdvals!(BigFloat.(ones(10,0)))
ERROR: DimensionMismatch("length of diagonal vector is 0, length of off-diagonal vector is 0")
Stacktrace:
 [1] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:11 [inlined]
 [2] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6/LinearAlgebra/src/bidiag.jl:17 [inlined]
 [3] Bidiagonal
   @ /home/julia/julia/usr/share/julia/stdlib/v1.6
--- REAL FIX (ground truth)
diff --git a/src/svd.jl b/src/svd.jl
index 098a982..656ebb0 100644
--- a/src/svd.jl
+++ b/src/svd.jl
@@ -161,6 +161,10 @@ function __svd!(
 ) where {T<:Real}
 
     n = size(B, 1)
+    if n == 0
+        # Exit early in the empty case
+        return nothing
+    end
     n1 = 1
     n2 = n
     d = B.dv

--- Proposal A
## Root cause
`svdvals!` directly calls `bidiagonalize!` on the input matrix.  
When the matrix has a zero dimension (e.g. `10×0`), `bidiagonalize!` tries to build a `Bidiagonal` object with empty diagonal and off‑diagonal vectors. The `Bidiagonal` constructor in Base checks that the lengths of those vectors satisfy `length(d) == length(e)+1` and throws a `DimensionMismatch` when both are zero.  

For matrices whose element type is a `BlasFloat` (e.g. `Float32`) the generic fallback in Base’s `svdvals!` is used, which already handles the empty case, but the generic implementation in **GenericLinearAlgebra** does not have this guard. Consequently, calling `svdvals!` on an empty matrix with a non‑`BlasFloat` element type crashes.

## Fix
Add an early‑exit for empty matrices in the generic `svdvals!` implementation. The fix is confined to `src/svd.jl` and does **not** affect the algorithm for non‑empty inputs.

```diff
--- a/src/svd.jl
+++ b/src/svd.jl
@@
-"""
-    svdvals!(A::AbstractMatrix{T}; tol::T = eps(T), debug::Bool = false) where {T}
-
-Compute the singular values of `A` in‑place, returning a vector of length
-`min(size(A)...)`.  The matrix `A` is overwritten.
-
-`tol` is a tolerance used for deflation; `debug` prints intermediate
-information when true.
-"""
-function svdvals!(A::AbstractMatrix{T}; tol::T = eps(T), debug::Bool = false) where {T}
-    m, n = size(A)
-    # ... existing implementation ...
-    B = bidiagonalize!(A)               # ← crashes for empty matrices
-    # ... rest of the algorithm ...
-end
+"""
+    svdvals!(A::AbstractMatrix{T}; tol::T = eps(T), debug::Bool = false) where {T}
+
+Compute the singular values of `A` in‑place, returning a vector of length
+`min(size(A)...)`.  The matrix `A` is overwritten.
+
+`tol` is a tolerance used for deflation; `debug` prints intermediate
+information when true.
+"""
+function svdvals!(A::AbstractMatrix{T}; tol::T = eps(T), debug::Bool = false) where {T}
+    m, n = size(A)
+
+    # --------------------------------------------------------------------
+    # Special‑case empty matrices.
+    # For an empty matrix the set of singular values is empty, regardless of
+    # the element type.  Returning an empty `Vector{T}` avoids calling
+    # `bidiagonalize!`, which would attempt to construct a `Bidiagonal` with
+    # zero‑length diagonal/off‑diagonal vectors and raise a `DimensionMismatch`.
+    # --------------------------------------------------------------------
+    if m == 0 || n == 0
+        return Vector{T}()   # empty vector of the appropriate element type
+    end
+
+    # --------------------------------------------------------------------
+    # Normal (non‑empty) path – unchanged from the original implementation.
+    # --------------------------------------------------------------------
+    B = bidiagonalize!(A)               # safe now that we know m,n > 0
+    # ... existing algorithm continues unchanged ...
+    # (the rest of the function body is left as‑is)
+    # --------------------------------------------------------------------
+end
```

*Explanation of the changes*  

1. **Early‑exit block** – Checks `m == 0 || n == 0`. If either dimension is zero, the singular‑value set is empty, so we return `Vector{T}()` (an empty vector with the correct element type).  
2. **Commentary** – Added a detailed comment describing why the guard is necessary and how it prevents the `DimensionMismatch` coming from `Bidiagonal`.  
3. **Placement** – The guard is inse
--- Proposal B
## Root cause
`svdvals!` always calls `bidiagonalize!` which builds a `Bidiagonal` object from the diagonal and off‑diagonal vectors of the bidiagonal reduction.  
For an empty matrix (`size(A,1)==0` or `size(A,2)==0`) both vectors have length 0, and the generic `Bidiagonal` constructor checks that `length(e) == length(d)-1`.  
When `length(d)==0` this condition becomes `0 == -1` and a `DimensionMismatch` is thrown.  
The BLAS‑based path (`<:BlasFloat`) bypasses this because the LAPACK driver has a special case for zero‑size matrices, but the generic path (used for `Float16`, `BigFloat`, etc.) does not, causing the failure reported.

## Fix
Add an early‑exit for empty inputs in the generic SVD implementation so that no bidiagonalisation is attempted and an empty vector of the appropriate element type is returned.

```diff
--- a/src/svd.jl
+++ b/src/svd.jl
@@
-"""
-    svdvals!(A::AbstractMatrix{T}; tol::T = zero(T), debug::Bool = false) -> Vector{T}
-
-Compute the singular values of `A` in‑place, overwriting `A`.  The algorithm
-works for any element type `T` that supports the required arithmetic
-operations.  For `T <: BlasFloat` a LAPACK driver is used; otherwise a
-generic bidiagonalisation + QR iteration is performed.
-"""
-function svdvals!(A::AbstractMatrix{T}; tol::T = zero(T), debug::Bool = false) where {T}
-    m, n = size(A)
-    # Reduce to bidiagonal form
-    B = bidiagonalize!(A)
-    # Compute singular values from the bidiagonal matrix
-    s = _svdvals_from_bidiagonal!(B, tol=tol, debug=debug)
-    return s
-end
+"""
+    svdvals!(A::AbstractMatrix{T}; tol::T = zero(T), debug::Bool = false) -> Vector{T}
+
+Compute the singular values of `A` **in‑place**, overwriting `A`.  The algorithm
+works for any element type `T` that supports the required arithmetic
+operations.  For `T <: BlasFloat` a LAPACK driver is used; otherwise a
+generic bidiagonalisation + QR iteration is performed.
+
+The generic implementation previously failed for empty matrices (e.g. `10×0`
+or `0×5`) because it attempted to construct a `Bidiagonal` object with empty
+diagonal and off‑diagonal vectors, which violates the invariant
+`length(e) == length(d)-1`.  The fix adds an early‑exit that returns an empty
+vector of the correct element type when either dimension of `A` is zero.
+"""
+function svdvals!(A::AbstractMatrix{T}; tol::T = zero(T), debug::Bool = false) where {T}
+    # ----------------------------------------------------------------------
+    # Special case: empty matrix
+    # ----------------------------------------------------------------------
+    if isempty(A)   # true if any dimension is zero
+        return T[]   # an empty vector of the appropriate element type
+    end
+
+    m, n = size(A)
+    # Reduce to bidiagonal form
+    B = bidiagonalize!(A)
+    # Compute singular values from the bidiagonal matrix
+    s = _svdvals_from_bidiagonal!(B, tol=tol, debug=debug)
+    return s
+end
```

If the repository also defines `svd!` (the full decomposition) we add the same guard there to keep behaviour consistent:

```diff
--- a/src/svd.jl
+++ b/src/svd.jl
@@
 function svd!(A::AbstractMatrix{T}; full::Bool = false, tol::T = zero(T), debug::Bool = false) where {T}
-    m, n = size(A)
-    B = bidiagonalize!(A)
-    U, s, Vt = _svd_from_bidiagonal!(B; full=full, tol=tol, debug=debug)
-    return SVD{T}(U, s, Vt)
+    # ----------------------------------------------------------------------
+    # Special case: empty matrix
+    # ----------

=== TASK marshmallow-code__apispec-361 | repo marshmallow-code/apispec | python
--- Issue
Should marshmallow helpers respect `only`, `exclude`?
When introspecting `Nested` fields, we don't check for the `only` or `exclude` attributes. For example:

``` python
class FooSchema(Schema):
    bar = fields.Str()
    baz = fields.Str()

class BobSchema(Schema):
    foo = fields.Nested(FooSchema, only=('bar', ))
```

If we call `schema2jsonschema(BobSchema)`, the nested `foo` will include `bar` and `baz` fields, even though `baz` will never be included in this case. Which isn't necessarily a problem, unless `baz` is required:

``` python
class FooSchema(Schema):
    bar = fields.Str()
    baz = fields.Str(required=True)
```

In this case, users of apispec will likely return JSON that wouldn't validate against the definition schema, since there's going to be a missing required field. I haven't actually encountered this situation, and I don't know how often it's going to come up--just wanted to raise for discussion.

--- REAL FIX (ground truth)
diff --git a/apispec/ext/marshmallow/openapi.py b/apispec/ext/marshmallow/openapi.py
index b18a21a..a254079 100644
--- a/apispec/ext/marshmallow/openapi.py
+++ b/apispec/ext/marshmallow/openapi.py
@@ -447,25 +447,22 @@ class OpenAPIConverter(object):
 
         :param schema: schema to add to the spec
         """
-
-        schema_cls = self.resolve_schema_class(schema)
-        name = self.schema_name_resolver(schema_cls)
-
-        if not name:
-            try:
-                return self.schema2jsonschema(schema)
-            except RuntimeError:
-                raise APISpecError(
-                    'Name resolver returned None for schema {schema} which is '
-                    'part of a chain of circular referencing schemas. Please'
-                    ' ensure that the schema_name_resolver passed to'
-                    ' MarshmallowPlugin returns a string for all circular'
-                    ' referencing schemas.'.format(schema=schema),
-                )
-
         schema_instance = resolve_schema_instance(schema)
         schema_key = make_schema_key(schema_instance)
         if schema_key not in self.refs:
+            schema_cls = self.resolve_schema_class(schema)
+            name = self.schema_name_resolver(schema_cls)
+            if not name:
+                try:
+                    return self.schema2jsonschema(schema)
+                except RuntimeError:
+                    raise APISpecError(
+                        'Name resolver returned None for schema {schema} which is '
+                        'part of a chain of circular referencing schemas. Please'
+                        ' ensure that the schema_name_resolver passed to'
+                        ' MarshmallowPlugin returns a string for all circular'
+                        ' referencing schemas.'.format(schema=schema),
+                    )
             name = get_unique_schema_name(self.spec.components, name)
             self.spec.components.schema(
                 name,

--- Proposal A
## Root cause
`apispec.ext.marshmallow.utils.field2property` (used by `schema2jsonschema`) creates a nested schema reference without taking the `only` or `exclude` arguments of a `fields.Nested` field into account.  
When a `Nested` field is declared as:

```python
foo = fields.Nested(FooSchema, only=('bar',))
```

the helper builds a reference to `FooSchema` unchanged, so the generated JSON‑Schema contains **all** of `FooSchema`’s fields (`bar` *and* `baz`).  
If the nested schema has required fields that are omitted by `only`/`exclude`, the resulting JSON‑Schema is inaccurate and can cause validation errors.

## Fix
* Update the marshmallow helper to instantiate the nested schema with the `only` and `exclude` arguments supplied to the `Nested` field.
* Use this instantiated schema when generating the JSON‑Schema (either as an inline schema or a `$ref`).
* Add a small utility `_instantiate_nested_schema` to keep the logic tidy.

```diff
--- a/apispec/ext/marshmallow/utils.py
+++ b/apispec/ext/marshmallow/utils.py
@@
 from marshmallow import fields, Schema
@@
 def field2property(field, **kwargs):
@@
-    if isinstance(field, fields.Nested):
-        # NOTE: we do not currently respect `only` or `exclude` on Nested fields.
-        # This leads to the nested schema being rendered with all its fields,
-        # even when the user asked for a subset via `only` or `exclude`.
-        # See issue #XXX.
-        nested_schema = field.schema
-        if isinstance(nested_schema, type):
-            # a Schema class – we need an instance to introspect its fields
-            nested_schema = nested_schema()
-        # Resolve the schema reference (may be a $ref or an inline definition)
-        return {'$ref': ref_for_schema(nested_schema, **kwargs)}
+    if isinstance(field, fields.Nested):
+        # -----------------------------------------------------------------
+        # Nested fields can be configured with `only` and `exclude` which
+        # limit the fields that are (de)serialized.  The original implementation
+        # ignored those arguments and always used the full schema, producing
+        # an incorrect JSON‑Schema when the nested schema contains required
+        # fields that are omitted by `only`/`exclude`.
+        #
+        # We now instantiate the nested schema with the same `only` and
+        # `exclude` values that the Nested field was created with.  Marshmallow
+        # accepts these arguments on schema construction, so the resulting
+        # schema instance correctly reflects the subset of fields.
+        # -----------------------------------------------------------------
+
+        def _instantiate_nested_schema(nested):
+            """
+            Return a schema *instance* that respects the `only` and `exclude`
+            arguments of the surrounding ``fields.Nested`` field.
+            """
+            # ``field.schema`` may be a Schema class or an already‑instantiated
+            # Schema.  In both cases we create a *new* instance with the proper
+            # ``only``/``exclude`` arguments so that we do not mutate the user’s
+            # original schema object.
+            schema_cls = nested if isinstance(nested, type) else nested.__class__
+            # Preserve the ``many`` flag that may have been set on the field.
+            many = getattr(field, "many", False)
+            return schema_cls(
+                only=getattr(field, "only", None),
+                exclude=getattr(field, "exc
--- Proposal B
## Root cause
`apispec` builds a JSON‑Schema definition for a `marshmallow.fields.Nested` field by directly using the *original* schema class (`FooSchema` in the example).  
The `only` and `exclude` arguments that were passed to the `Nested` field are stored on the field instance (`field.only`, `field.exclude`) but are never taken into account when the nested schema is converted. Consequently the generated definition contains **all** fields of the nested schema, including required ones that are not actually exposed by the `only`/`exclude` filter. This leads to JSON that would not validate against the generated schema.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/apispec/ext/marshmallow/plugin.py
+++ b/apispec/ext/marshmallow/plugin.py
@@
 class MarshmallowPlugin(BasePlugin):
@@
     def map_field(self, field, **kwargs):
         """Map a marshmallow field to an OpenAPI schema property."""
@@
         if isinstance(field, fields.Nested):
-            # NOTE: we ignore `only`/`exclude` on the Nested field.
-            # This means the generated schema may contain fields that are
-            # not actually present in the serialized output.
-            nested_schema = field.schema
+            # Respect `only` and `exclude` that were supplied to the Nested
+            # field.  Marshmallow stores these values on the field instance
+            # (`field.only`, `field.exclude`).  We create a *restricted* schema
+            # instance that reflects the same view that will be used at dump/
+            # load time.  This ensures that required fields that are filtered
+            # out are not emitted in the generated JSON‑Schema.
+
+            # Start with the original schema (could be a class or an instance)
+            nested_schema = field.schema
+
+            # `field.only` may be a tuple/list of field names or ``None``.
+            # `field.exclude` may be a tuple/list of field names or ``None``.
+            # Marshmallow's ``only``/``exclude`` methods accept *positional*
+            # arguments, so we unpack the collections.
+            if getattr(field, "only", None):
+                # ``only`` overrides ``exclude`` – marshmallow applies it first.
+                # Convert to a list to allow multiple calls without mutating
+                # the original collection.
+                only_fields = list(field.only)
+                nested_schema = nested_schema.only(*only_fields)
+
+            if getattr(field, "exclude", None):
+                exclude_fields = list(field.exclude)
+                nested_schema = nested_schema.exclude(*exclude_fields)
+
+            # At this point ``nested_schema`` is a *Schema* instance that
+            # reflects the view defined by the Nested field.
+
+            # Recursively map the (possibly restricted) nested schema.
+            # ``self.map_schema`` returns a reference name (or a full schema
+            # dict if ``inline`` is True).  We keep the original behaviour
+            # regarding ``inline`` handling.
+            if kwargs.get("inline"):
+                # Inline the nested schema definition.
+                return self.map_schema(nested_schema, **kwargs)
+            else:
+                # Use a $ref to the component schema.
+                ref = self.map_schema(nested_schema, **kwargs)
+                return {"$ref": ref}
 
-            # Recursively map the nested schema.
-            if kwargs.get("inline"):
-            

=== TASK iterative__dvc-4642 | repo iterative/dvc | python
--- Issue
BUG: JSON parsing error for `dvc metrics show`
## Bug Report

I have a simple JSON of test metrics, in file `scores.json`:
```json
{
    "test_1": {
        "precision": 0,
        "f1": 0.0
    },
    "test_2": {
        "fpr": 0.0
    },
    "notes": "if precision is zero, there were zero positive predictions; if f1 is zero, something went very wrong"
}
```

I expect the key "notes" to fail, but the numeric keys should show up. However, they don't:
```console
$ dvc metrics show -v scores.json
2020-09-28 17:16:09,022 DEBUG: Check for update is enabled.
2020-09-28 17:16:09,024 DEBUG: fetched: [(3,)]                        
2020-09-28 17:16:09,029 DEBUG: Could not parse 'precision' metric from 'scores.json' at '' due to its unsupported type: 'int'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'f1' metric from 'scores.json' at '' due to its unsupported type: 'float'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'test_1' metric from 'scores.json' at '' due to its unsupported type: 'dict'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'fpr' metric from 'scores.json' at '' due to its unsupported type: 'float'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'test_2' metric from 'scores.json' at '' due to its unsupported type: 'dict'
2020-09-28 17:16:09,029 DEBUG: Could not parse 'notes' metric from 'scores.json' at '' due to its unsupported type: 'str'
2020-09-28 17:16:09,030 DEBUG: fetched: [(11243,)]
2020-09-28 17:16:09,031 ERROR: failed to show metri
--- REAL FIX (ground truth)
diff --git a/dvc/command/metrics.py b/dvc/command/metrics.py
--- a/dvc/command/metrics.py
+++ b/dvc/command/metrics.py
@@ -11,7 +11,7 @@
 DEFAULT_PRECISION = 5
 
 
-def show_metrics(
+def _show_metrics(
     metrics, all_branches=False, all_tags=False, all_commits=False
 ):
     from dvc.utils.diff import format_dict
@@ -21,22 +21,25 @@ def show_metrics(
     # specified as `targets` in `repo.metrics.show` didn't contain any metrics.
     missing = metrics.pop(None, None)
 
+    lines = []
     for branch, val in metrics.items():
         if all_branches or all_tags or all_commits:
-            logger.info(f"{branch}:")
+            lines.append(f"{branch}:")
 
         for fname, metric in val.items():
             if not isinstance(metric, dict):
-                logger.info("\t{}: {}".format(fname, str(metric)))
+                lines.append("\t{}: {}".format(fname, str(metric)))
                 continue
 
-            logger.info(f"\t{fname}:")
+            lines.append(f"\t{fname}:")
             for key, value in flatten(format_dict(metric)).items():
-                logger.info(f"\t\t{key}: {value}")
+                lines.append(f"\t\t{key}: {value}")
 
     if missing:
         raise BadMetricError(missing)
 
+    return "\n".join(lines)
+
 
 class CmdMetricsBase(CmdBase):
     UNINITIALIZED = True
@@ -58,12 +61,14 @@ def run(self):
 
                 logger.info(json.dumps(metrics))
             else:
-                show_metrics(
+                table = _show_metrics(
                     metrics,
                     self.args.all_branches,
                     self.args.all_tags,
                     self.args.all_commits,
                 )
+                if table:
+                    logger.info(table)
         except DvcException:
             logger.exception("failed to show metrics")
             return 1
diff --git a/dvc/command/repro.py b/dvc/command/repro.py
--- a/dvc/command/repro.py
+++ b/dvc/command/repro.py
@@ -4,7 +4,7 @@
 
 from dvc.command import completion
 from dvc.command.base import CmdBase, append_doc_link
-from dvc.command.metrics import show_metrics
+from dvc.command.metrics import _show_metrics
 from dvc.command.status import CmdDataStatus
 from dvc.dvcfile import PIPELINE_FILE
 from dvc.exceptions import DvcException
@@ -51,7 +51,8 @@ def run(self):
 
                 if self.args.metrics:
                     metrics = self.repo.metrics.show()
-                    show_metrics(metrics)
+                    logger.info(_show_metrics(metrics))
+
             except DvcException:
                 logger.exception("")
                 ret = 1
diff --git a/dvc/repo/metrics/show.py b/dvc/repo/metrics/show.py
--- a/dvc/repo/metrics/show.py
+++ b/dvc/repo/metrics/show.py
@@ -48,7 +48,7 @@ def _extract_metrics(metrics, path, rev):
     ret = {}
     for key, val in metrics.items():
         m = _extract_metrics(val, path, rev)
-        if m:
+        if m not in (None, {}):
             ret[key] = m
         els
--- Proposal A
## Root cause
`dvc metrics show` parses metric files by iterating only over the **top‑level** keys of a JSON/YAML document.  
When a metric value is nested inside a dictionary (e.g. `test_1.precision`) the parser treats the
containing dictionary as an unsupported type and aborts the whole operation, emitting debug
messages such as:

```
Could not parse 'precision' metric … due to its unsupported type: 'int'
```

Thus *all* numeric leaf values are discarded and the command fails, even though the file is
perfectly valid. The fix is to make the metric collector **recursively walk** the loaded
structure, flattening nested dictionaries (and optionally lists) and keeping only scalar
numeric leaf values (`int` or `float`). Non‑numeric leaves (e.g. the `"notes"` string) should be
silently ignored rather than causing a failure.

## Fix
```diff
--- a/dvc/metrics/collect.py
+++ b/dvc/metrics/collect.py
@@
-from typing import Any, Dict, Iterable, List, Tuple, Union
+from typing import Any, Dict, Iterable, List, Tuple, Union
@@
-def _parse_metric_file(
-    path: str,
-    typ: str,
-    logger: Logger,
-) -> Tuple[Dict[str, Union[int, float]], List[str]]:
-    \"\"\"Parse a metric file and return a mapping of metric names to values.\n-
-    Returns a tuple of (metrics, errors) where *metrics* is a dict of metric
-    names to numeric values and *errors* is a list of error messages for
-    unsupported entries.\n-    \"\"\n-
-    try:\n-        with open(path, \"r\", encoding=\"utf-8\") as fobj:\n-            raw = json.load(fobj) if typ == \"json\" else yaml.safe_load(fobj)\n-    except Exception as exc:  # pragma: no cover – defensive\n-        logger.debug(\"Failed to load metric file %s: %s\", path, exc)\n-        return {}, [f\"could not load {path}: {exc}\"]\n-\n-    metrics: Dict[str, Union[int, float]] = {}\n-    errors: List[str] = []\n-\n-    if not isinstance(raw, dict):\n-        errors.append(f\"metric file {path} does not contain a mapping\")\n-        return {}, errors\n-\n-    for key, val in raw.items():\n-        if isinstance(val, (int, float)):\n-            metrics[key] = val\n-        else:\n-            errors.append(\n-                f\"Could not parse '{key}' metric from '{path}' at '' due to its unsupported type: '{type(val).__name__}'\"\n-            )\n-            logger.debug(errors[-1])\n-\n-    return metrics, errors\n+def _parse_metric_file(\n+    path: str,\n+    typ: str,\n+    logger: Logger,\n+) -> Tuple[Dict[str, Union[int, float]], List[str]]:\n+    \"\"\"Parse a metric file and return a flat mapping of *metric_path* → value.\n+\n+    The function now **recursively** walks dictionaries (and lists) so that nested\n+    numeric values are collected.  Only ``int`` and ``float`` leaf values are kept;\n+    other leaf types are ignored with a debug message.  The returned ``errors``\n+    list contains messages for *unrecoverable* problems (e.g. file‑read errors or\n+    a top‑level non‑mapping), not for each unsupported leaf.\n+    \"\"\"\n+\n+    # ---------------------------------------------------------------------\n+    # Load the raw file content.\n+    # ---------------------------------------------------------------------\n+    try:\n+        with open(path, \"r\", encoding=\"utf-8\") as fobj:\n+            raw = json.load(fobj) if typ == \"json\" else yaml.safe_load(fobj)\n+    except Exception as exc:  # pragma: no cover – defensive\n+        logger.debug(\"Failed to load metric file %s: %s\", path, exc)\n
--- Proposal B
## Root cause
`dvc metrics show` parses JSON metric files by walking the JSON object and only accepting values of a **whitelisted type**.  
The whitelist mistakenly omitted the built‑in numeric types (`int` and `float`).  
Consequently every leaf value – even valid numbers – was reported as “unsupported type”, and the command always failed with *Could not parse metric files*.

## Fix
Add the missing numeric types to the whitelist and make the parser recurse into dictionaries so that nested numeric values are collected correctly.

```diff
--- a/dvc/repo/metrics.py
+++ b/dvc/repo/metrics.py
@@
-def _parse_metric(value, path=""):
-    """Parse a metric value from a JSON/YAML file.
-
-    Returns a dict mapping metric paths to scalar values.
-    Unsupported types are ignored (debug‑logged by the caller)."""
-    # NOTE: only scalar numeric values are supported.
-    if isinstance(value, (list, tuple)):
-        # Lists are not supported – they would need an index in the metric name.
-        logger.debug("Could not parse metric from %r at %r due to its unsupported type: %s",
-                     value, path, type(value).__name__)
-        return {}
-
-    if isinstance(value, dict):
-        # Dictionaries are not supported – they would need a dot‑separated name.
-        logger.debug("Could not parse metric from %r at %r due to its unsupported type: %s",
-                     value, path, type(value).__name__)
-        return {}
-
-    # At this point we only have scalar values.
-    if isinstance(value, (int, float)):
-        # NOTE: this branch was *incorrectly* marked as unsupported in the
-        # original implementation, causing the bug.
-        logger.debug("Could not parse metric from %r at %r due to its unsupported type: %s",
-                     value, path, type(value).__name__)
-        return {}
-
-    # Anything else (e.g. str, bool, None) is unsupported.
-    logger.debug("Could not parse metric from %r at %r due to its unsupported type: %s",
-                 value, path, type(value).__name__)
-    return {}
+def _parse_metric(value, path=""):
+    """Parse a metric value from a JSON/YAML file.
+
+    Returns a dict mapping metric *paths* (dot‑separated for nested keys) to
+    scalar numeric values.  Unsupported leaf types are ignored – the caller
+    logs a debug message for them.
+    """
+    # Containers -------------------------------------------------------------
+    if isinstance(value, dict):
+        # Recurse into dictionaries, building a dotted path.
+        metrics: dict = {}
+        for k, v in value.items():
+            sub_path = f"{path}.{k}" if path else k
+            metrics.update(_parse_metric(v, sub_path))
+        return metrics
+
+    if isinstance(value, (list, tuple)):
+        # Lists are not supported because they would require an index in the
+        # metric name.  Emit a debug message and drop the whole list.
+        logger.debug(
+            "Could not parse metric from %r at %r due to its unsupported type: %s",
+            value,
+            path,
+            type(value).__name__,
+        )
+        return {}
+
+    # Scalars ---------------------------------------------------------------
+    # Numeric scalar values are the only supported leaf type.
+    if isinstance(value, (int, float)):
+        return {path: value}
+
+    # Everything else (str, bool, None, etc.) is unsupported.
+    logger.debug(
+        "Could not parse metric from %r at %r due to its unsupported t

=== TASK iterative__dvc-5005 | repo iterative/dvc | python
--- Issue
dvc 1.10.2 does not provide the extra 'webdavs'
## Bug Report
I wanted to test the webdav protocol. The command `dvc push -r webdavremote` (where webdavremote is the name of my remote). That gave me the useful output:
```
ERROR: failed to push data to the cloud - URL '***snipped***' is supported but requires these missing dependencies: ['webdavclient3']. If you have installed dvc using pip, choose one of these options to proceed: 

    1) Install specific missing dependencies:
        pip install webdavclient3
    2) Install dvc package that includes those missing dependencies: 
        pip install 'dvc[webdavs]'
    3) Install dvc package with all possible dependencies included: 
        pip install 'dvc[all]'

If you have installed dvc from a binary package and you are still seeing this message, please report it to us using https://github.com/iterative/dvc/issues. Thank you!
```
But the installation of the webdav extension using pip3 leads to the following first lines:
```
pip3 install 'dvc[webdavs]'
Requirement already satisfied: dvc[webdavs] in ${HOME}/.local/lib/python3.7/site-packages (1.10.2)
  dvc 1.10.2 does not provide the extra 'webdavs'
Requirement already satisfied: dictdiffer>=0.8.1 in ${HOME}/.local/lib/python3.7/site-packages (from dvc[webdavs]) (0.8.1)
Requirement already satisfied: toml>=0.10.1 in ${HOME}/.local/lib/python3.7/site-packages (from dvc[webdavs]) (0.10.2)
```
Hence the installation did nothing. I am a bit puzzled, why there
--- REAL FIX (ground truth)
diff --git a/dvc/tree/base.py b/dvc/tree/base.py
--- a/dvc/tree/base.py
+++ b/dvc/tree/base.py
@@ -70,7 +70,7 @@ def __init__(self, repo, config):
         self.repo = repo
         self.config = config
 
-        self._check_requires(config)
+        self._check_requires()
 
         shared = config.get("shared")
         self._file_mode, self._dir_mode = self.SHARED_MODE_MAP[shared]
@@ -107,31 +107,44 @@ def get_missing_deps(cls):
 
         return missing
 
-    def _check_requires(self, config):
+    def _check_requires(self):
+        from ..scheme import Schemes
+        from ..utils import format_link
+        from ..utils.pkg import PKG
+
         missing = self.get_missing_deps()
         if not missing:
             return
 
-        url = config.get("url", f"{self.scheme}://")
-        msg = (
-            "URL '{}' is supported but requires these missing "
-            "dependencies: {}. If you have installed dvc using pip, "
-            "choose one of these options to proceed: \n"
-            "\n"
-            "    1) Install specific missing dependencies:\n"
-            "        pip install {}\n"
-            "    2) Install dvc package that includes those missing "
-            "dependencies: \n"
-            "        pip install 'dvc[{}]'\n"
-            "    3) Install dvc package with all possible "
-            "dependencies included: \n"
-            "        pip install 'dvc[all]'\n"
-            "\n"
-            "If you have installed dvc from a binary package and you "
-            "are still seeing this message, please report it to us "
-            "using https://github.com/iterative/dvc/issues. Thank you!"
-        ).format(url, missing, " ".join(missing), self.scheme)
-        raise RemoteMissingDepsError(msg)
+        url = self.config.get("url", f"{self.scheme}://")
+
+        scheme = self.scheme
+        if scheme == Schemes.WEBDAVS:
+            scheme = Schemes.WEBDAV
+
+        by_pkg = {
+            "pip": f"pip install 'dvc[{scheme}]'",
+            "conda": f"conda install -c conda-forge dvc-{scheme}",
+        }
+
+        cmd = by_pkg.get(PKG)
+        if cmd:
+            link = format_link("https://dvc.org/doc/install")
+            hint = (
+                f"To install dvc with those dependencies, run:\n"
+                "\n"
+                f"\t{cmd}\n"
+                "\n"
+                f"See {link} for more info."
+            )
+        else:
+            link = format_link("https://github.com/iterative/dvc/issues")
+            hint = f"Please report this bug to {link}. Thank you!"
+
+        raise RemoteMissingDepsError(
+            f"URL '{url}' is supported but requires these missing "
+            f"dependencies: {missing}. {hint}"
+        )
 
     @classmethod
     def supported(cls, config):

--- Proposal A
## Root cause
`dvc[webdavs]` is referenced in the code (e.g. in the error message that is shown when the *webdav* remote is used) but the package’s metadata never declared an extra named **webdavs**.  
In `setup.cfg` only the `webdav` extra was defined, so `pip` cannot resolve `dvc[webdavs]` and prints:

```
dvc 1.10.2 does not provide the extra 'webdavs'
```

Because the extra is missing, installing `dvc[webdavs]` does nothing and the required dependency
`webdavclient3` is not installed.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/setup.cfg
+++ b/setup.cfg
@@
 [options.extras]
 # Existing extras (truncated for brevity)
 azure = azure-storage-blob>=12.0.0
 gdrive = pydrive2>=1.8.0
 s3 = boto3>=1.9.0
@@
- webdav = webdavclient3>=1.0.0
+ webdav = webdavclient3>=1.0.0
+ # The HTTPS variant of the WebDAV remote.  It uses the same underlying
+ # library (`webdavclient3`) but is advertised to the user as a separate
+ # extra so that the error message can suggest `dvc[webdavs]`.
+ webdavs = webdavclient3>=1.0.0
 all =
     azure
     gdrive
@@
-    webdav
+    webdav
+    webdavs
```

**Explanation of the changes**

1. **Declare the missing extra** – Add a new entry `webdavs = webdavclient3>=1.0.0` under `[options.extras]`.  
   This makes the extra visible to `pip` and satisfies the runtime check that looks for the `webdavs` extra.

2. **Include the extra in the `all` meta‑extra** – Append `webdavs` to the list of packages installed by `dvc[all]` so that users who install the “all” bundle also get the HTTPS WebDAV support.

No code changes are required elsewhere; the remote implementation already imports `webdavclient3` lazily and will work once the dependency is installed.

## How to verify
1. **Re‑install the package** in a clean environment:

   ```bash
   pip uninstall -y dvc
   pip install 'dvc[webdavs]'
   ```

   The installation should finish without the warning *“dvc 1.10.2 does not provide the extra 'webdavs'”*.

2. **Check that the dependency is installed**:

   ```bash
   pip show webdavclient3
   ```

   It should be present.

3. **Run a DVC command that requires the extra** (e.g. push to a WebDAV‑S remote):

   ```bash
   dvc push -r my_webdav_remote
   ```

   The command should no longer abort with the missing‑dependency error; instead it should proceed (or fail only for genuine connection problems).

4. **Confirm that `dvc[all]` still pulls in the extra**:

   ```bash
   pip uninstall -y dvc
   pip install 'dvc[all]'
   pip show webdavclient3   # should be installed
   ```

All steps passing demonstrates that the extra is now correctly provided.
--- Proposal B
## Root cause
`dvc` 1.10.2 ships its optional dependencies through *extras* defined in the packaging metadata (``setup.cfg`` / ``pyproject.toml``).  
The extra that enables the WebDAV S remote is **missing** from that metadata – the file only contains an entry called ``webdav`` (singular) while the code and the user‑facing error message refer to the extra ``webdavs`` (plural).  

When `pip install "dvc[webdavs]"` is executed, `pip` looks for an extra named ``webdavs`` in the package’s metadata. Because it cannot find one, it reports:

```
dvc 1.10.2 does not provide the extra 'webdavs'
```

Consequently the required dependency ``webdavclient3`` is never installed, and the remote cannot be used.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/setup.cfg
+++ b/setup.cfg
@@
 [options.extras_require]
 # Existing extras …
 # NOTE: the extra for the WebDAV S remote was mistakenly named ``webdav``.
- webdav = 
-    webdavclient3>=1.0.0
+ # The correct extra name used throughout the code base and documentation is
+ # ``webdavs`` (plural).  Keep the old name for backward compatibility but map it
+ # to the same requirement list.
+ webdav = 
+    webdavclient3>=1.0.0
+ webdavs = 
+    webdavclient3>=1.0.0
```

If the project uses a ``pyproject.toml`` instead of ``setup.cfg`` (newer builds), apply the analogous change:

```diff
--- a/pyproject.toml
+++ b/pyproject.toml
@@
 [project.optional-dependencies]
- webdav = ["webdavclient3>=1.0.0"]
+ # Keep the historic singular name for compatibility.
+ webdav = ["webdavclient3>=1.0.0"]
+ # Add the correctly‑named extra that the CLI expects.
+ webdavs = ["webdavclient3>=1.0.0"]
```

*Explanation of the changes*

1. **Add the missing ``webdavs`` extra** – maps to the same requirement list as the existing (but undocumented) ``webdav`` extra.
2. **Retain the original ``webdav`` extra** – this preserves compatibility for any external scripts that may have used the singular name.
3. No code changes are required; the remote implementation already imports ``webdavclient3`` lazily and will work once the dependency is installed.

## How to verify
1. **Re‑install the package** in a clean virtual environment to ensure the new metadata is used:

   ```bash
   python -m venv /tmp/dvc-test
   source /tmp/dvc-test/bin/activate
   pip install --upgrade pip setuptools wheel
   pip install "dvc[webdavs]==1.10.2"
   ```

2. **Check that the extra is now recognized**:

   ```bash
   pip show dvc | grep -i "Extras"
   # Expected output should contain: Extras: ..., webdavs, ...
   ```

3. **Confirm that the required dependency is installed**:

   ```bash
   pip list | grep webdavclient3
   # Should show webdavclient3 with a version >= 1.0.0
   ```

4. **Run a DVC command that uses the WebDAV S remote**:

   ```bash
   dvc remote add -d webdavremote webdavs://user:pass@host/path
   dvc push -r webdavremote
   ```

   The command should no longer emit the “missing dependencies” error. Instead, it should either succeed or fail for reasons unrelated to missing optional packages (e.g., authentication), confirming that the extra is correctly installed.

5. **Optional regression test** – run the project's test suite (if available) for the WebDAV remote:

   ```bash
   pytest tests/test_remote_webdav.py
   ```

   All tests should pass.

If all the steps above succeed, the issue is resolved.

=== TASK PyThaiNLP__pythainlp-399 | repo PyThaiNLP/pythainlp | python
--- Issue
countthai not cover all cases
**Describe the bug**
Input countthai('123') or countthai(' ') or countthai('1 2 3 4') create error instead of zero? 

**To Reproduce**
Steps to reproduce the behavior:
1. from pythainlp.util import countthai
2. input either '123' or ' '

**Expected behavior**
it should return zero?
--- REAL FIX (ground truth)
diff --git a/pythainlp/util/thai.py b/pythainlp/util/thai.py
index 1a78dc32..b141b260 100644
--- a/pythainlp/util/thai.py
+++ b/pythainlp/util/thai.py
@@ -127,7 +127,7 @@ def countthai(text: str, ignore_chars: str = _DEFAULT_IGNORE_CHARS) -> float:
         # output: 0.0
     """
     if not text or not isinstance(text, str):
-        return 0
+        return 0.0
 
     if not ignore_chars:
         ignore_chars = ""
@@ -143,4 +143,7 @@ def countthai(text: str, ignore_chars: str = _DEFAULT_IGNORE_CHARS) -> float:
 
     num_count = len(text) - num_ignore
 
+    if num_count == 0:
+        return 0.0
+
     return (num_thai / num_count) * 100

--- Proposal A
## Root cause
`countthai` calculates the proportion of Thai characters by dividing the number of Thai characters by the length of the *effective* text (text after removing ignored characters such as whitespace).  
When the input contains **no characters that are counted** – e.g. only digits, spaces or an empty string – the effective length becomes `0`. The original implementation performed the division unconditionally, causing a **`ZeroDivisionError`** instead of returning `0.0` as the logical result.

## Fix
```diff
--- a/pythainlp/util/__init__.py
+++ b/pythainlp/util/__init__.py
@@
-import re
-
-def countthai(text):
-    # original implementation
-    thai = re.findall(r'[\u0E00-\u0E7F]', text)
-    total = len(re.sub(r'[\u0E00-\u0E7F]', '', text))
-    return len(thai) / total
+import re
+
+def countthai(text: str) -> float:
+    """
+    Return the proportion of Thai characters in *text*.
+
+    The function counts Thai characters (Unicode range U+0E00‑U+0E7F) and
+    divides that count by the number of characters that are taken into
+    account for the calculation.  When the input does not contain any
+    characters that are considered (e.g. an empty string, a string that
+    consists only of spaces or non‑Thai symbols), the original code raised
+    a ``ZeroDivisionError``.  The function now returns ``0.0`` in those
+    situations, matching the expected behaviour described in the issue.
+    """
+    # Validate input type
+    if not isinstance(text, str):
+        raise TypeError("text must be a string")
+
+    # ------------------------------------------------------------------
+    # 1. Count Thai characters
+    # ------------------------------------------------------------------
+    thai_chars = re.findall(r'[\u0E00-\u0E7F]', text)
+    thai_count = len(thai_chars)
+
+    # ------------------------------------------------------------------
+    # 2. Determine the denominator (effective length)
+    # ------------------------------------------------------------------
+    # Historically the function ignored whitespace characters when
+    # computing the denominator.  We keep that behaviour by stripping all
+    # whitespace before measuring the length.
+    effective_text = re.sub(r'\s+', '', text)
+    effective_len = len(effective_text)
+
+    # ------------------------------------------------------------------
+    # 3. Guard against division by zero
+    # ------------------------------------------------------------------
+    if effective_len == 0:
+        # No characters to evaluate → proportion of Thai characters is 0
+        return 0.0
+
+    # 4. Return the proportion as a float
+    return thai_count / effective_len
```

### What was changed
1. Added a proper docstring and type hint.
2. Added input‑type validation.
3. Re‑implemented the calculation:
   * Count Thai characters with a regex.
   * Compute the *effective* length after removing whitespace (the same
     behaviour as the original implementation).
4. **Guarded against division by zero** – when the effective length is `0`,
   the function now returns `0.0` instead of raising an exception.
5. Ensured the function always returns a `float`.

## How to verify
1. **Run the repository’s test suite** to ensure no existing tests break.  
   ```bash
   pytest -q
   ```

2. **Add/execute the following ad‑hoc checks** (or include them as new unit tests):
   ```python
   from pythainlp.util import countthai

   assert countthai('123') == 0.0
   assert counttha
--- Proposal B
## Root cause
`countthai` was implemented in a way that assumed the input string only contained Thai characters or was non‑empty.  
When the function received digits, spaces or an empty string it tried to process the text (e.g. by indexing or splitting) and raised an exception instead of simply returning `0`.  

## Fix
```diff
--- a/pythainlp/util.py
+++ b/pythainlp/util.py
@@
-import re
-
-def countthai(text):
-    \"\"\"Count Thai characters in *text*.\n-
-
-    The original implementation assumed *text* always contained Thai characters\n-    and would raise an exception for digits, spaces or an empty string.\n-    \"\"\"\n-    # original buggy code (illustrative) -------------------------------------------------\n-    # thai_chars = [c for c in text if u'\u0E00' <= c <= u'\u0E7F']\n-    # return len(thai_chars)\n-    # -----------------------------------------------------------------------------------\n-    # The above raised errors for non‑string inputs or strings without Thai characters.\n-    # -------------------------------------------------------------------------------\n-    # Fixed implementation follows.\n-    pass\n+import re
+
+def countthai(text):
+    """
+    Count the number of Thai characters in *text*.
+
+    The function is now defensive:
+    * If *text* is not a ``str`` (e.g. ``None`` or an integer) it is safely converted\n+      to ``str``; if conversion fails the function returns ``0``.\n+    * It works correctly for strings that contain only digits, spaces, punctuation\n+      or are completely empty – it simply returns ``0`` instead of raising an error.\n+\n+    Parameters
+    ----------
+    text : Any
+        The input to be examined.\n+\n+    Returns
+    -------
+    int
+        The count of characters whose Unicode code point lies in the Thai block\n+        (U+0E00‑U+0E7F).\n+    \"\"\"\n+\n+    # Ensure we are working with a string; fall back to ``0`` on failure.\n+    if not isinstance(text, str):\n+        try:\n+            text = str(text)\n+        except Exception:  # pragma: no cover – extremely unlikely path\n+            return 0\n+\n+    # Use a compiled regular expression for efficiency and clarity.\n+    # The pattern matches any character in the Thai Unicode block.\n+    thai_pattern = re.compile(r\"[\\u0E00-\\u0E7F]\")\n+    return len(thai_pattern.findall(text))
```

## How to verify
1. **Run the library’s test suite** (if available) after the change to ensure no existing tests break.  
2. **Manual checks** in a Python REPL:

```python
from pythainlp.util import countthai

assert countthai('123') == 0          # digits only
assert countthai(' ') == 0            # single space
assert countthai('1 2 3 4') == 0      # digits with spaces
assert countthai('สวัสดี') == 6       # pure Thai
assert countthai('สวัสดี 123') == 6   # mixed Thai and non‑Thai
assert countthai('') == 0             # empty string
assert countthai(None) == 0           # non‑string input
```

All assertions should pass without raising an exception.  
If the project includes CI, push the change and confirm that the CI pipeline succeeds.

=== TASK gleam-lang__gleam-3486 | repo gleam-lang/gleam | rust
--- Issue
Language Server: Label access completion only works with public types
The language server doesn't show any suggestion for fields when a record's type isn't public:

![image](https://github.com/user-attachments/assets/3d752e6b-71b2-48e0-ad07-b2b14bf45d7e)

If we set `Person` as public, suggestions show up.

![image](https://github.com/user-attachments/assets/06dd6878-e89a-4780-bd58-e4b246e957b7)

--- REAL FIX (ground truth)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 4ca403c17..64b5695b9 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -11,3 +11,7 @@
 ### Language Server
 
 ### Bug Fixes
+
+- Fix a bug that caused record accessors for private types to not be completed
+  by the LSP, even when in the same module.
+  ([Ameen Radwan](https://github.com/Acepie))
diff --git a/compiler-core/src/analyse.rs b/compiler-core/src/analyse.rs
index b0b61bfb1..bac174009 100644
--- a/compiler-core/src/analyse.rs
+++ b/compiler-core/src/analyse.rs
@@ -279,8 +279,6 @@ impl<'a, A> ModuleAnalyzer<'a, A> {
         // items.
         env.module_types
             .retain(|_, info| info.module == self.module_name);
-        env.accessors
-            .retain(|_, accessors| accessors.publicity.is_importable());
 
         // Ensure no exported values have private types in their type signature
         for value in env.module_values.values() {
diff --git a/compiler-core/src/language_server/completer.rs b/compiler-core/src/language_server/completer.rs
index 64c2e4277..c7d006db1 100644
--- a/compiler-core/src/language_server/completer.rs
+++ b/compiler-core/src/language_server/completer.rs
@@ -657,7 +657,8 @@ where
         match type_.as_ref() {
             Type::Named { name, module, .. } => importable_modules
                 .get(module)
-                .and_then(|i| i.accessors.get(name)),
+                .and_then(|i| i.accessors.get(name))
+                .filter(|a| a.publicity.is_importable() || module == &self.module.name),
             _ => None,
         }
     }
diff --git a/compiler-core/src/type_/expression.rs b/compiler-core/src/type_/expression.rs
index 7c81f183f..9a967ca31 100644
--- a/compiler-core/src/type_/expression.rs
+++ b/compiler-core/src/type_/expression.rs
@@ -2206,7 +2206,10 @@ impl<'a, 'b> ExprTyper<'a, 'b> {
                 .environment
                 .importable_modules
                 .get(module)
-                .and_then(|module| module.accessors.get(name)),
+                .and_then(|module| module.accessors.get(name))
+                .filter(|a| {
+                    a.publicity.is_importable() || module == &self.environment.current_module
+                }),
 
             _something_without_fields => return Err(unknown_field(vec![])),
         }

--- Proposal A
## Root cause
The language server only offers label‑access completions when the record’s type is **public**.  
The completion code checks `type.is_public()` before emitting field suggestions, so private
record types (i.e., types that are not exported from the module) never get their fields
included, even when the code that accesses the record lives in the same module where the
type is defined. This makes the behaviour inconsistent with the rest of the language
where private records can be used freely inside their defining module.

## Fix
Update the completion logic to allow field suggestions for any type that is either
public **or** defined in the same module as the source file being edited.

```diff
--- a/gleam-language-server/src/completion.rs
+++ b/gleam-language-server/src/completion.rs
@@
 fn label_access_completions(
     ctx: &CompletionContext,
     expr: &UntypedExpr,
 ) -> Vec<CompletionItem> {
@@
-    // Resolve the type of the expression we are accessing a label on.
-    let typ = ctx.analysis.type_of(expr);
-
-    // Only suggest fields for public types – this is the buggy behaviour.
-    if let Some(type_id) = typ {
-        let type_info = ctx.analysis.types.get(type_id);
-        if type_info.is_public() {
-            return fields_from_type(type_info, ctx);
-        }
-    }
-
-    Vec::new()
+    // Resolve the type of the expression we are accessing a label on.
+    let typ = ctx.analysis.type_of(expr);
+
+    // We want to suggest fields for:
+    //   * public types (the original behaviour) and
+    //   * private types that are defined in the same module as the file we are
+    //     completing in.  Private types from other modules are not accessible,
+    //     so we must not suggest their fields.
+    if let Some(type_id) = typ {
+        let type_info = ctx.analysis.types.get(type_id);
+
+        // `type_info.module` is the module where the type was defined.
+        // `ctx.module_id` is the module of the file we are completing in.
+        let is_accessible = type_info.is_public()
+            || type_info.module == ctx.module_id;
+
+        if is_accessible {
+            return fields_from_type(type_info, ctx);
+        }
+    }
+
+    Vec::new()
 }
 
 /// Collect the fields of a record type and turn them into completion items.
 fn fields_from_type(type_info: &TypeInfo, ctx: &CompletionContext) -> Vec<CompletionItem> {
@@
```

### What changed
* Added a check for `type_info.module == ctx.module_id` so that private types defined in the
  current module are considered *accessible*.
* Kept the original public‑type behaviour unchanged.
* The rest of the function (`fields_from_type`) remains unchanged; it now receives the
  appropriate `type_info` for both public and same‑module private types.

## How to verify
1. **Build the language server**  
   ```bash
   cargo build --workspace --all-features
   ```

2. **Create a test project** (or use an existing one) with a private record:

   ```gleam
   // src/main.gleam
   type person = { name: String, age: Int }

   pub fn demo() {
     let p = person.{name: "Bob", age: 30}
     p. // <-- trigger completion here
   }
   ```

3. **Start the language server** (e.g., via `gleam lsp` or the editor integration).

4. **Trigger completion** after `p.`:
   * Expected: the completion list contains `name` and `age`.
   * Previously it would have been empty because `person` is private.

5. **Confirm that public records still work**:

   ```gleam
   pub type public_person = {
--- Proposal B
## Root cause
The language‑server’s record‑field completion logic only shows suggestions when the record’s type is **public**.  
The code that builds the completion list checks `type.is_public()` (or the equivalent visibility flag) and discards the fields otherwise.  
When the record is defined in the same module as the completion request, the type is still accessible even if it is private, but the visibility check incorrectly filters it out. This makes label‑access completion work only for public types.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/language-server/src/completion.rs
+++ b/language-server/src/completion.rs
@@
-    // Only suggest fields for public record types.
-    if !record_type.is_public() {
-        return Vec::new();
-    }
-
-    let fields = record_type
-        .fields()
-        .iter()
-        .map(|field| CompletionItem {
-            label: field.name.clone(),
-            kind: Some(CompletionItemKind::FIELD),
-            detail: Some(format!("{}: {}", field.name, field.typ)),
-            ..Default::default()
-        })
-        .collect();
-
-    fields
+    // ----------------------------------------------------------------------
+    // NOTE: Record field completions should be offered when the record type
+    // is *accessible* from the current module.  A type is accessible if:
+    //   1. It is public (exported), **or**
+    //   2. It is defined in the same module that the user is editing.
+    // The previous implementation only checked `is_public()`, which caused
+    // completions to disappear for private record types even when they are
+    // used inside their own module.
+    // ----------------------------------------------------------------------
+
+    // `module_name` is the name of the module currently being edited,
+    // passed down from the request handling code.
+    let same_module = record_type.module() == current_module_name;
+    let accessible = record_type.is_public() || same_module;
+
+    if !accessible {
+        // The type is private and defined in a different module – no
+        // completions should be offered.
+        return Vec::new();
+    }
+
+    // At this point the type is accessible, so we can safely expose its fields.
+    let fields = record_type
+        .fields()
+        .iter()
+        .map(|field| CompletionItem {
+            label: field.name.clone(),
+            kind: Some(CompletionItemKind::FIELD),
+            detail: Some(format!("{}: {}", field.name, field.typ)),
+            ..Default::default()
+        })
+        .collect();
+
+    fields
```

**Explanation of the changes**

1. **Added an accessibility check** – a record type is considered accessible if it is public **or** if it originates from the same module that the completion request is for (`same_module`).  
2. **Introduced `current_module_name`** – the completion routine already receives the module context (e.g., via `request.module_name` or a similar field). The diff assumes a variable `current_module_name: &str` is in scope; if not, the surrounding code should be updated to pass it in (the surrounding function already has access to the request’s module name).  
3. **Retained the early‑return for inaccessible types** – now private types from other modules are correctly filtered out, while private types from the current module are allowed.  
4. **Added a detailed comment** explaining the reasoning, making future maintenance easier.

If the functi

=== TASK briannesbitt__carbon-2745 | repo briannesbitt/Carbon | php
--- Issue
diffForHumans broken after commit 0f0a245
<!--
    🛑 DON'T REMOVE ME.
    This issue template applies to all
      - bug reports,
      - feature proposals,
      - and documentation requests

    Having all those informations will allow us to know exactly
    what you expect and answer you faster and precisely (answer
    that matches your Carbon version, PHP version and usage).
    
    Note: Comments between <!- - and - -> won't appear in the final
    issue (See [Preview] tab).
-->
Hello,

I encountered an issue with the following code:
<!--
    ⚠️ Your code below (not the code from inside Carbon library)
    Be sure it's dependency-free and enough to reproduce the issue
    when we run it on:
    https://play.phpsandbox.io/embed/nesbot/carbon
-->
```php
\Carbon\Carbon::yesterday()->diffForHumans([
  'syntax' => \Carbon\CarbonInterface::DIFF_ABSOLUTE,
  'parts' => 3,
  'skip' => 's',
]);
```

Carbon version: 2.64.0
<!--
    ⚠️ Run the command `composer show nesbot/carbon` to get "versions"
-->

PHP version: 7.4.33

<!--
    Use `echo phpversion();`
    to get PHP version.

    Some issues can depends on your context, settings,
    macros, timezone, language. So to be sure the code
    you give is enough to reproduce your bug, try it
    first in:
    https://play.phpsandbox.io/embed/nesbot/carbon?input=%3C%3Fphp%0A%0Aecho%20Carbon%3A%3Aparse('2018-06-01')-%3EsubDay()-%3Emonth%3B

    You can use the [Options] button to chang
--- REAL FIX (ground truth)
diff --git a/src/Carbon/Traits/Difference.php b/src/Carbon/Traits/Difference.php
index 4f1fceca..a9868ebf 100644
--- a/src/Carbon/Traits/Difference.php
+++ b/src/Carbon/Traits/Difference.php
@@ -844,7 +844,7 @@ trait Difference
         $parts = min(7, max(1, (int) $parts));
         $skip = \is_array($syntax) ? ($syntax['skip'] ?? []) : [];
 
-        return $this->diffAsCarbonInterval($other, false, $skip)
+        return $this->diffAsCarbonInterval($other, false, (array) $skip)
             ->setLocalTranslator($this->getLocalTranslator())
             ->forHumans($syntax, (bool) $short, $parts, $options ?? $this->localHumanDiffOptions ?? static::getHumanDiffOptions());
     }

--- Proposal A
**## Root cause**

`Carbon::diffForHumans()` was refactored in commit **0f0a245** to accept a richer set of parameters (named arguments, option‑array, etc.).  
During that refactor the handling of the *options* argument became incorrect: when an associative array is passed as the **only** argument (as in the issue), the method mistakenly treats the array as the `$syntax` parameter and later passes the value of the `'skip'` key (`'s'`) as the third argument to `diffAsCarbonInterval()`.  

`diffAsCarbonInterval()` expects its third argument to be an **array**, so a string (`'s'`) triggers the type‑error:

```
Argument 3 passed to Carbon\Carbon::diffAsCarbonInterval() must be of the type array, string given
```

The bug is isolated to the parameter‑normalisation logic inside `diffForHumans()`.

---

**## Fix**

```diff
--- a/src/Carbon/Traits/Difference.php
+++ b/src/Carbon/Traits/Difference.php
@@
-    public function diffForHumans($other = null, $syntax = null, $short = false, $parts = null, $options = null)
-    {
-        // Legacy handling – if the second argument is an array we treat it as options.
-        if (is_array($syntax)) {
-            $options = $syntax;
-            $syntax = null;
-        }
-
-        // Legacy handling – if the third argument is an array we treat it as options.
-        if (is_array($short)) {
-            $options = $short;
-            $short = false;
-        }
-
-        // Legacy handling – if the fourth argument is an array we treat it as options.
-        if (is_array($parts)) {
-            $options = $parts;
-            $parts = null;
-        }
-
-        // Normalise options to an array.
-        $options = $options ?: [];
-
-        // Merge explicit parameters into the options array.
-        if ($syntax !== null) {
-            $options['syntax'] = $syntax;
-        }
-
-        if ($short !== false) {
-            $options['short'] = $short;
-        }
-
-        if ($parts !== null) {
-            $options['parts'] = $parts;
-        }
-
-        $interval = $this->diffAsCarbonInterval($other, $syntax, $options);
-
-        return $interval->forHumans($options);
-    }
+    public function diffForHumans($other = null, $syntax = null, $short = false, $parts = null, $options = null)
+    {
+        /**
+         * The original implementation tried to be clever by moving “array”
+         * arguments into the $options slot, but it failed when the array
+         * contained keys that were later interpreted as the $short or $parts
+         * parameters (e.g. the “skip” key).  The result was that a string was
+         * forwarded to diffAsCarbonInterval() as the $options argument, causing
+         * a TypeError.
+         *
+         * The fixed implementation follows a deterministic order:
+         *   1. Detect legacy calls where $syntax, $short or $parts are arrays
+         *      and promote them to $options.
+         *   2. Ensure $options is always an array.
+         *   3. Merge the explicit $syntax, $short and $parts values into the
+         *      options array (they override any values that may already be set).
+         *   4. Pass the *options* array (never a scalar) to diffAsCarbonInterval().
+         */
+
+        // 1️⃣ Legacy handling – promote any array passed in the positional slots.
+        if (is_array($syntax)) {
+            $options = $syntax;
+            $syntax = null;
+        }
+
+        if (is_array($short)) {
+            $options = $short;
+         
--- Proposal B
## Root cause
`Carbon::diffForHumans()` changed its signature in commit **0f0a245**.  
The method now expects the *options* array as the **third** argument, while older
calls (and the public documentation) allowed passing the options as the **first**
argument when no `$other` date was supplied.

When the options array is given as the first argument, the method treats it as the
`$other` parameter (a `CarbonInterface` or `DateTimeInterface`). Later it forwards
that value to `diffAsCarbonInterval()` which now type‑hints the third argument as
`array`. Because a string (or in this case an array that was mis‑interpreted) is
passed, PHP throws:

```
TypeError: Argument 3 passed to Carbon\Carbon::diffAsCarbonInterval() must be of the type array, string given
```

So the backward‑compatible handling of an *options‑only* call was lost, causing the
exception.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/src/Carbon/Traits/Difference.php
+++ b/src/Carbon/Traits/Difference.php
@@
-    public function diffForHumans($other = null, $syntax = null, $options = [])
+    /**
+     * Get the difference in a human readable format.
+     *
+     * The method historically accepted the options array as the **first**
+     * argument when no `$other` date was supplied.  After commit 0f0a245 the
+     * signature was changed to `($other = null, $syntax = null, $options = [])`,
+     * breaking that usage.
+     *
+     * To retain backward compatibility we detect the old calling style:
+     *   - if the first argument is an array,
+     *   - and the second and third arguments are their default values,
+     *   we treat the first argument as the options array.
+     *
+     * This mirrors the behaviour that existed before the refactor and fixes the
+     * TypeError that occurs when the options array is incorrectly forwarded to
+     * `diffAsCarbonInterval()`.
+     *
+     * @param CarbonInterface|DateTimeInterface|string|null $other   The date to compare with.
+     * @param int|bool|null                                 $syntax  The syntax flag.
+     * @param array                                         $options Options array.
+     *
+     * @return string
+     */
+    public function diffForHumans($other = null, $syntax = null, $options = [])
     {
-        // existing implementation …
+        // -----------------------------------------------------------------
+        // Backward‑compatibility shim for the old “options‑only” call style.
+        // -----------------------------------------------------------------
+        if (is_array($other) && $syntax === null && $options === []) {
+            $options = $other;
+            $other   = null;
+        }
+
+        // -----------------------------------------------------------------
+        // The rest of the original implementation (unchanged).
+        // -----------------------------------------------------------------
+        // NOTE: The original code is left untouched; only the shim above
+        //       was added.  The method eventually calls
+        //       $this->diffAsCarbonInterval($other, $syntax, $options)
+        //       which now always receives a proper array for $options.
+
+        // ---- BEGIN ORIGINAL CODE (kept verbatim) -----------------------
+        // (the original body of diffForHumans from the library)
+        // ---- END ORIGINAL CODE -----------------------------------------
     }
```

*Explanation of the changes*

1. **Doc

=== TASK PyCQA__flake8-bugbear-316 | repo PyCQA/flake8-bugbear | python
--- Issue
New check: visit_ function with no generic_visit/visit
This is kind of super niche, and mostly just of interest to flake8 plugin writers in particular, but I accidentally forgot to continue `visit`'ing in a couple functions I wrote recently in flake8-trio and realized it'd be incredibly handy - and very easy to implement - an optional check where if a function name matches `visit_*` it raises a warning if there's no `self.visit(...)` or `self.generic_visit(...)` within it's body.

I imagine it'd be nice to have that in the CI for flake8-bugbear itself as well, even though your structure makes it less likely than it is for flake8-trio currently.
--- REAL FIX (ground truth)
diff --git a/README.rst b/README.rst
index 498b000..53a3dab 100644
--- a/README.rst
+++ b/README.rst
@@ -205,6 +205,10 @@ to raise a ``ValueError`` if the arguments are exhausted at differing lengths. T
 was added in Python 3.10, so don't enable this flag for code that should work on <3.10.
 For more information: https://peps.python.org/pep-0618/
 
+**B906**: ``visit_`` function with no further call to a ``visit`` function. This is often an error, and will stop the visitor from recursing into the subnodes of a visited node. Consider adding a call ``self.generic_visit(node)`` at the end of the function.
+Will only trigger on function names where the part after ``visit_`` is a valid ``ast`` type with a non-empty ``_fields`` attribute.
+This is meant to be enabled by developers writing visitors using the ``ast`` module, such as flake8 plugin writers.
+
 **B950**: Line too long. This is a pragmatic equivalent of
 ``pycodestyle``'s ``E501``: it considers "max-line-length" but only triggers
 when the value has been exceeded by **more than 10%**. You will no
@@ -302,6 +306,11 @@ MIT
 Change Log
 ----------
 
+Future
+~~~~~~~~~
+
+* Add B906: ``visit_`` function with no further calls to a ``visit`` function. (#313)
+
 22.12.6
 ~~~~~~~~~
 
diff --git a/bugbear.py b/bugbear.py
index 2d2b7cd..90d238a 100644
--- a/bugbear.py
+++ b/bugbear.py
@@ -413,6 +413,7 @@ class BugBearVisitor(ast.NodeVisitor):
         self.check_for_b018(node)
         self.check_for_b019(node)
         self.check_for_b021(node)
+        self.check_for_b906(node)
         self.generic_visit(node)
 
     def visit_ClassDef(self, node):
@@ -969,6 +970,27 @@ class BugBearVisitor(ast.NodeVisitor):
         ):
             self.errors.append(B905(node.lineno, node.col_offset))
 
+    def check_for_b906(self, node: ast.FunctionDef):
+        if not node.name.startswith("visit_"):
+            return
+
+        # extract what's visited, only error if it's a valid ast subclass
+        # with a non-empty _fields attribute - which is what's iterated over in
+        # ast.NodeVisitor.generic_visit
+        class_name = node.name[len("visit_") :]
+        class_type = getattr(ast, class_name, None)
+        if class_type is None or not getattr(class_type, "_fields", None):
+            return
+
+        for n in itertools.chain.from_iterable(ast.walk(nn) for nn in node.body):
+            if isinstance(n, ast.Call) and (
+                (isinstance(n.func, ast.Attribute) and "visit" in n.func.attr)
+                or (isinstance(n.func, ast.Name) and "visit" in n.func.id)
+            ):
+                break
+        else:
+            self.errors.append(B906(node.lineno, node.col_offset))
+
 
 def compose_call_path(node):
     if isinstance(node, ast.Attribute):
@@ -990,7 +1012,7 @@ class NameFinder(ast.NodeVisitor):
 
     names = attr.ib(default=attr.Factory(dict))
 
-    def visit_Name(self, node):
+    def visit_Name(self, node):  # noqa: B906 # names don't contain other names
        
--- Proposal A
## Root cause
`flake8-bugbear` does not currently check that a method whose name matches the pattern `visit_*` actually performs a visitor call (`self.visit(...)` or `self.generic_visit(...)`).  This can let a plugin author forget to continue the AST traversal, which is a common source of bugs when writing `ast.NodeVisitor`‑based plugins.

## Fix
Add a new check that walks every `FunctionDef` node, and when the function:

* is a method (first argument is named `self`), **and**
* its name starts with `visit_`,

it verifies that the function body contains at least one call of the form `self.visit(...)` or `self.generic_visit(...)`.  
If no such call is found, emit a new BugBear warning `B950`.

```diff
--- a/flake8_bugbear/checks.py
+++ b/flake8_bugbear/checks.py
@@
 from collections import defaultdict
 import ast
 import re
@@
 from . import utils
@@
 class BugBearVisitor(ast.NodeVisitor):
@@
     def __init__(self, tree, filename, options):
         self.tree = tree
         self.filename = filename
         self.options = options
         self.errors = []
         self._seen = defaultdict(list)
@@
         self.generic_visit(node)
         self._check_unused_imports(node)
         self._check_unused_variables(node)
+
+    # ------------------------------------------------------------------
+    # New check: visit_* methods must call self.visit or self.generic_visit
+    # ------------------------------------------------------------------
+    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # pragma: no cover
+        """
+        If a method is named ``visit_*`` and its first argument is ``self``,
+        ensure that the body contains a call to ``self.visit`` or
+        ``self.generic_visit``.  If not, emit ``B950``.
+        """
+        # Continue walking the tree first so that nested definitions are also
+        # visited (the check itself does not need to stop traversal).
+        self.generic_visit(node)
+
+        # Only consider methods (first argument named ``self``) – free functions
+        # named ``visit_*`` are ignored.
+        if not node.args.args:
+            return
+        first_arg = node.args.args[0]
+        if not isinstance(first_arg, ast.arg) or first_arg.arg != "self":
+            return
+
+        # Name must start with ``visit_``.
+        if not node.name.startswith("visit_"):
+            return
+
+        # Walk the function body looking for a call ``self.visit`` or
+        # ``self.generic_visit``.
+        has_visit_call = False
+        for child in ast.walk(node):
+            if isinstance(child, ast.Call):
+                func = child.func
+                if (
+                    isinstance(func, ast.Attribute)
+                    and isinstance(func.value, ast.Name)
+                    and func.value.id == "self"
+                    and func.attr in ("visit", "generic_visit")
+                ):
+                    has_visit_call = True
+                    break
+
+        if not has_visit_call:
+            self.error_from_node(
+                node,
+                "B950",
+                "B950 visit_* method missing a call to self.visit or self.generic_visit",
+            )
*** End of File
--- a/flake8_bugbear/__init__.py
+++ b/flake8_bugbear/__init__.py
@@
 __all__ = ["BugbearChecker"]
@@
 # Mapping of error codes to human‑readable messages.
 # Existing codes omitted for brevity …
+ERRORS = {
+    # ... existing error definitions ...
+    "B950": "visit_* method m
--- Proposal B
## Root cause
`flake8-bugbear` does not currently check whether a user‑defined method whose name matches the pattern `visit_*` actually performs a traversal by calling `self.visit(...)` or `self.generic_visit(...)`.  This can lead to silent logical errors in AST‑based plugins (e.g. forgetting to continue the walk).  No existing check looks for this pattern, so the issue is missed.

## Fix
Add a new check that runs on every `FunctionDef` node:

* If the function name starts with `visit_`
* Walk the function body looking for a call of the form `self.visit(...)` or `self.generic_visit(...)`
* If none is found, emit a new `B902` warning.

```diff
--- a/flake8_bugbear/checks.py
+++ b/flake8_bugbear/checks.py
@@
 import ast
 import re
 from collections import defaultdict
 from typing import Any, Callable, Iterable, List, Tuple, Union
 
@@
 class BugbearVisitor(ast.NodeVisitor):
@@
     def __init__(self, tree: ast.AST, filename: str) -> None:
         self.errors: List[Tuple[int, int, str]] = []
         self.filename = filename
         self.tree = tree
         super().__init__()
@@
     def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
         """Run all function‑level checks."""
@@
         self.check_unused_variable(node)
         self.check_duplicate_exceptions(node)
         self.check_mutable_default_argument(node)
         self.check_unnecessary_list_comprehension(node)
         self.check_use_of_strip(node)
         self.check_use_of_isinstance(node)
         self.check_useless_else_on_loop(node)
         self.check_assert_in_finally(node)
         self.check_assert_in_except(node)
         self.check_assert_in_else(node)
         self.check_assert_in_try(node)
         self.check_assert_in_with(node)
         self.check_assert_in_lambda(node)
         self.check_assert_in_comprehension(node)
         self.check_assert_in_generator(node)
         self.check_assert_in_async_def(node)
         self.check_assert_in_class(node)
         self.check_assert_in_function(node)
+        # -----------------------------------------------------------------
+        # New check: ensure a `visit_*` method actually calls `self.visit` or
+        # `self.generic_visit`.  This helps plugin authors avoid forgetting to
+        # continue the AST walk.
+        # -----------------------------------------------------------------
+        if node.name.startswith("visit_"):
+            # Walk the whole function body looking for a call like
+            # `self.visit(...)` or `self.generic_visit(...)`.
+            calls_self_visit = any(
+                isinstance(call, ast.Call)
+                and isinstance(call.func, ast.Attribute)
+                and isinstance(call.func.value, ast.Name)
+                and call.func.value.id == "self"
+                and call.func.attr in ("visit", "generic_visit")
+                for call in ast.walk(node)
+            )
+            if not calls_self_visit:
+                # B902 – `visit_*` method missing call to `self.visit` or
+                # `self.generic_visit`.
+                self.error(
+                    node,
+                    "B902 function '{0}' is a `visit_*` method but does not call "
+                    "`self.visit` or `self.generic_visit`".format(node.name),
+                )
 
         # Continue generic traversal
         self.generic_visit(node)
```

### Explanation of the diff
* Imported `re` was already present; no new imports needed.
* Inside `visit_FunctionDef`

=== TASK kinnala__scikit-fem-667 | repo kinnala/scikit-fem | python
--- Issue
Examples providing your own mesh and retriving the basis functions?
Are there any examples for supplying your own mesh (for example from scipy Delaunay) and extracting basis functions of some degree? 

For example you might evaluate the basis functions at a set of points for fitting against data as a kind of smoothing spline.

--- REAL FIX (ground truth)
diff --git a/README.md b/README.md
index ec13b5ff..453498ed 100644
--- a/README.md
+++ b/README.md
@@ -257,6 +257,9 @@ with respect to documented and/or tested features.
 - Added: `ElementTriCCR` and `ElementTetCCR`, conforming Crouzeix-Raviart finite elements
 - Fixed: `Mesh.mirrored` returned a wrong mesh when a point other than the origin was used
 - Fixed: `MeshLine` constructor accepted only NumPy arrays and not plain Python lists
+- Fixed: `Mesh.element_finder` (and `CellBasis.probes`, `CellBasis.interpolator`) was not working properly for a small number of elements (<5) or a large number of input points (>1000)
+- Fixed: `MeshTet` and `MeshTri.element_finder` is are now more robust against degenerate elements
+- Fixed: `Mesh.element_finder` (and `CellBasis.probes`, `CellBasis.interpolator`) raises exception if the query point is outside of the domain
 
 ### [3.1.0] - 2021-06-18
 
diff --git a/docs/examples/ex28.py b/docs/examples/ex28.py
index 5c526815..987ce837 100644
--- a/docs/examples/ex28.py
+++ b/docs/examples/ex28.py
@@ -88,15 +88,7 @@ import numpy as np
 import pygmsh
 
 
-if version.parse(pygmsh.__version__) < version.parse('7.0.0'):
-    class NullContextManager():
-        def __enter__(self):
-            return None
-        def __exit__(self, *args):
-            pass
-    geometrycontext = NullContextManager()
-else:
-    geometrycontext = pygmsh.geo.Geometry()
+geometrycontext = pygmsh.geo.Geometry()
 
 halfheight = 1.
 length = 10.
@@ -110,17 +102,12 @@ peclet = 357.
 def make_mesh(halfheight: float,  # mm
               length: float,
               thickness: float) -> MeshTri:
-    with geometrycontext as g:
-        if version.parse(pygmsh.__version__) < version.parse('7.0.0'):
-            geom = pygmsh.built_in.Geometry()
-            geom.add_curve_loop = geom.add_line_loop
-        else:
-            geom = g
+    with geometrycontext as geom:
 
         points = []
         lines = []
 
-        lcar = halfheight / 2**2
+        lcar = halfheight / 2 ** 2
 
         for xy in [(0., halfheight),
                    (0., -halfheight),
@@ -155,10 +142,7 @@ def make_mesh(halfheight: float,  # mm
         geom.add_physical(geom.add_plane_surface(geom.add_curve_loop(
             [*lines[-3:], -lines[1]])), 'solid')
 
-        if version.parse(pygmsh.__version__) < version.parse('7.0.0'):
-            return from_meshio(pygmsh.generate_mesh(geom, dim=2))
-        else:
-            return from_meshio(geom.generate_mesh(dim=2))
+        return from_meshio(geom.generate_mesh(dim=2))
 
 mesh = from_file(Path(__file__).parent / 'meshes' / 'ex28.json')
 element = ElementTriP1()
diff --git a/skfem/mesh/mesh_line_1.py b/skfem/mesh/mesh_line_1.py
index ef85502c..e04afbda 100644
--- a/skfem/mesh/mesh_line_1.py
+++ b/skfem/mesh/mesh_line_1.py
@@ -74,6 +74,7 @@ class MeshLine1(Mesh):
         return np.max(np.abs(self.p[0, self.t[1]] - self.p[0, self.t[0]]))
 
     def element_finder(self, mapping=None):
+
         ix = np.argsort
--- Proposal A
## Root cause
`scikit‑fem` can only build a mesh from files or from arrays that are already in the internal “(dim, npoints)” layout.  Users that obtain a mesh from external libraries such as **SciPy Delaunay** receive point coordinates as an ``(npoints, dim)`` array and triangle indices as an ``(ntri, dim)`` array.  Passing these directly to ``MeshTri`` raises shape‑mismatch errors, so there is no straightforward way to *supply your own mesh*.

In addition, the public API does not expose a simple helper to evaluate the basis functions of a ``Basis`` object at an arbitrary set of points.  The documentation therefore lacks an example for “fit‑to‑data” style workflows (e.g. smoothing splines).

Both problems stem from missing convenience wrappers in the library:

1. **No constructor that accepts SciPy‑style point/triangle arrays** – users must manually transpose and reshape the data.
2. **No public ``Basis.eval`` method** – the low‑level ``Basis.evaluate`` exists only for internal use, making it hard to discover.

## Fix
*Add two convenience utilities*:

1. **`MeshTri.from_delaunay` / `MeshTri.from_arrays`** – class methods that accept SciPy‑style inputs, perform the necessary transpositions, and return a ready‑to‑use ``MeshTri`` instance.
2. **`Basis.eval`** – a thin public wrapper around the internal evaluation routine that accepts points in the user‑friendly ``(dim, npoints)`` layout and returns the matrix of basis values.

The changes are confined to the core modules, keep backward compatibility, and add comprehensive docstrings and type hints.

```diff
--- a/skfem/mesh.py
+++ b/skfem/mesh.py
@@
 class MeshTri(Mesh):
@@
     def __init__(self, points, cells, **kwargs):
         super().__init__(points, cells, **kwargs)
         self._check()
 
+    # --------------------------------------------------------------------- #
+    # Convenience constructors for user‑provided data
+    # --------------------------------------------------------------------- #
+    @classmethod
+    def from_arrays(cls, points: np.ndarray, cells: np.ndarray, **kwargs):
+        """
+        Construct a ``MeshTri`` from raw NumPy arrays.
+
+        Parameters
+        ----------
+        points : (n_points, dim) or (dim, n_points) array
+            Vertex coordinates.  If the shape is ``(n_points, dim)`` the
+            array is transposed to the internal ``(dim, n_points)`` layout.
+        cells : (n_cells, dim) or (dim, n_cells) integer array
+            Indices of the triangle vertices.  If the shape is
+            ``(n_cells, dim)`` the array is transposed.
+        **kwargs :
+            Additional keyword arguments passed to ``Mesh.__init__``.
+
+        Returns
+        -------
+        MeshTri
+            A triangular mesh ready for use with ``Basis`` etc.
+        """
+        points = np.asarray(points)
+        cells = np.asarray(cells, dtype=int)
+
+        # Accept both (n, dim) and (dim, n) conventions
+        if points.shape[0] != 2:
+            # Assume (n_points, dim) → transpose
+            points = points.T
+        if cells.shape[0] != 3:
+            # Assume (n_cells, 3) → transpose
+            cells = cells.T
+
+        return cls(points, cells, **kwargs)
+
+    @classmethod
+    def from_delaunay(cls, delaunay: "scipy.spatial.Delaunay", **kwargs):
+        """
+        Build a ``MeshTri`` directly from a ``scipy.spatial.Delaunay`` object.
+
+        This is the most common entry point for users who generate a mesh
+        wit
--- Proposal B
## Root cause
The repository does not contain an example that shows how to build a `Mesh` from an external triangulation (e.g., a `scipy.spatial.Delaunay` object) and how to evaluate the corresponding finite‑element basis functions at arbitrary points.  
While the core library already provides the necessary building blocks (`MeshTri`, `Basis`, `basis.evaluate`), there is no convenience wrapper to convert a Delaunay object into a `MeshTri`, nor is there any example script that demonstrates the whole workflow. This makes it hard for users to discover the feature and leads to the reported “missing example” issue.

## Fix
1. **Add a convenience constructor** `MeshTri.from_delaunay` in `skfem/mesh.py` that builds a `MeshTri` directly from a `scipy.spatial.Delaunay` object.  
2. **Add an example script** `examples/mesh_from_delaunay.py` that:
   * Generates a set of points,
   * Creates a Delaunay triangulation,
   * Converts it to a `MeshTri`,
   * Builds a `Basis` of a chosen degree,
   * Evaluates the basis functions on a user‑defined set of points,
   * Plots the basis functions (optional, using `matplotlib`).

```diff
--- a/skfem/mesh.py
+++ b/skfem/mesh.py
@@
 class MeshTri(Mesh):
@@
     def __init__(self, points, cells, **kwargs):
         super().__init__(points, cells, **kwargs)
         self._check_consistency()
         self._init_boundary()
 
+    @classmethod
+    def from_delaunay(cls, delaunay, **kwargs):
+        """
+        Construct a ``MeshTri`` from a ``scipy.spatial.Delaunay`` object.
+
+        Parameters
+        ----------
+        delaunay : scipy.spatial.Delaunay
+            The Delaunay triangulation to be converted.
+        **kwargs : dict
+            Additional keyword arguments passed to the ``MeshTri`` constructor.
+
+        Returns
+        -------
+        MeshTri
+            A mesh representing the same triangulation.
+        """
+        # ``delaunay.points`` contains the vertex coordinates (N x dim)
+        # ``delaunay.simplices`` contains the indices of the vertices for each triangle (M x 3)
+        points = np.asarray(delaunay.points, dtype=np.float64)
+        cells = np.asarray(delaunay.simplices, dtype=np.int64)
+        # Ensure the orientation of each triangle is positive (counter‑clockwise)
+        # The orientation test works for 2‑D meshes only.
+        if points.shape[1] == 2:
+            # Compute signed area for each cell
+            p0 = points[cells[:, 0]]
+            p1 = points[cells[:, 1]]
+            p2 = points[cells[:, 2]]
+            signed_area = 0.5 * ((p1[:, 0] - p0[:, 0]) * (p2[:, 1] - p0[:, 1]) -
+                                 (p2[:, 0] - p0[:, 0]) * (p1[:, 1] - p0[:, 1]))
+            # Flip cells with negative area
+            flip = signed_area < 0
+            cells[flip] = cells[flip][:, ::-1]
+        return cls(points, cells, **kwargs)
+
     @classmethod
     def from_vertices_and_cells(cls, vertices, cells, **kwargs):
         """Create a MeshTri from vertices and cells."""
         return cls(vertices, cells, **kwargs)
*** End of File
--- a/examples/mesh_from_delaunay.py
+++ b/examples/mesh_from_delaunay.py
@@
-"""
-Example: Build a mesh from scipy.spatial.Delaunay and evaluate basis functions.
-
-This script demonstrates:
-  * Creating a point cloud.
-  * Building a Delaunay triangulation with SciPy.
-  * Converting the triangulation to a scikit‑fem MeshTri.
-  * Constructing a finite‑element basis of a given degree.
-  * Evaluating the basis funct

=== TASK ipython__traitlets-773 | repo ipython/traitlets | python
--- Issue
Union trait does not parse correctly from strings
Union trait does not enumerate the types in the union when parsing from string which means that incorrect type gets inferred.

Correct inference of union types is desirable in https://github.com/jupyter-server/jupyter_server/pull/949 and https://github.com/ipython/ipython/pull/13745.
--- REAL FIX (ground truth)
diff --git a/traitlets/traitlets.py b/traitlets/traitlets.py
index 0eae5d1..4ec17da 100644
--- a/traitlets/traitlets.py
+++ b/traitlets/traitlets.py
@@ -2179,6 +2179,15 @@ class Union(TraitType):
         else:
             return Union(self.trait_types + [other])
 
+    def from_string(self, s):
+        for trait_type in self.trait_types:
+            try:
+                v = trait_type.from_string(s)
+                return trait_type.validate(None, v)
+            except TraitError:
+                continue
+        self.error(None, s)
+
 
 # -----------------------------------------------------------------------------
 # Basic TraitTypes implementations/subclasses

--- Proposal A
## Root cause
`Union` traits are used to accept values of several possible sub‑traits (e.g. `Int | Float`).  
When a value comes from a string (configuration file, CLI, etc.) the base `TraitType.from_string` implementation is called.  
The existing `Union.from_string` simply delegated to the *first* sub‑trait’s `from_string` method, ignoring the rest of the possible types.  
Consequently, strings that could be parsed by a later type in the union (e.g. `"3.14"` for `Float` when the union is `Int | Float`) raised a `TraitError` or were interpreted as the wrong type.  

The fix is to make `Union.from_string` (and the list‑variant `from_string_list`) try each contained trait in order, returning the first successful conversion.

---

## Fix
```diff
--- a/traitlets/traitlets.py
+++ b/traitlets/traitlets.py
@@
 class Union(TraitType):
@@
     def __init__(self, *traits, **kwargs):
         self.trait_types = traits
         super().__init__(**kwargs)
 
+    # NOTE: ``Union`` needs a custom ``from_string`` implementation that
+    # attempts to parse the input with each of its member traits in order.
+    # The previous implementation delegated to the first trait only,
+    # which caused incorrect parsing for values that were only valid for
+    # later traits in the union.
+    def from_string(self, s):
+        """
+        Parse a string ``s`` using the first sub‑trait that can successfully
+        convert it.
+
+        The method iterates over ``self.trait_types`` and calls each trait’s
+        ``from_string``.  If a sub‑trait raises a ``TraitError`` (or any
+        ``Exception`` that indicates a conversion failure), the next trait is
+        tried.  If none succeed, a ``TraitError`` is raised that lists the
+        attempted types.
+        """
+        # ``allow_none`` handling – keep the behaviour of the base class.
+        if self.allow_none and s in ("None", "none"):
+            return None
+
+        last_error = None
+        for sub_trait in self.trait_types:
+            try:
+                # ``sub_trait`` may be a ``TraitType`` instance or a class.
+                # ``TraitType`` objects are callable (they implement ``__call__``)
+                # but we need the *type* to access its ``from_string`` method.
+                # ``sub_trait`` is always an instance (created in ``Union.__init__``),
+                # so we can call its ``from_string`` directly.
+                return sub_trait.from_string(s)
+            except TraitError as e:
+                # Store the error to report if all attempts fail.
+                last_error = e
+            except Exception as e:  # pragma: no cover – defensive
+                last_error = TraitError(
+                    f"Error parsing {s!r} as {sub_trait}: {e}"
+                )
+        # If we get here, no sub‑trait could parse the string.
+        trait_names = ", ".join(
+            getattr(t, "name", getattr(t, "__class__", type(t)).__name__)
+            for t in self.trait_types
+        )
+        raise TraitError(
+            f"Unable to parse {s!r} as any of the Union types: {trait_names}"
+        ) from last_error
+
+    # ``from_string_list`` is used by the CLI loader for list‑valued traits.
+    # The same logic as ``from_string`` applies, but the input is a list of
+    # strings that should be joined before parsing.
+    def from_string_list(self, l):
+        """
+        Parse a list of strings ``l`` by joining them with commas and delegating
+      
--- Proposal B
## Root cause
`Union` traits delegate string parsing to the first sub‑trait only.  
When a value is supplied as a string (e.g. from a config file), the
`Union.from_string` implementation calls `self.trait_type.from_string`
where `self.trait_type` is the *first* type in the union.  
Consequently, if the first type can parse the string (or raises a generic
`TraitError` that is swallowed), the union never tries the remaining
types, leading to incorrect type inference and missing enumeration of
the possible types.

## Fix
```diff
--- a/traitlets/traitlets.py
+++ b/traitlets/traitlets.py
@@
 class Union(TraitType):
@@
     def __init__(self, *traits, **kwargs):
         self.trait_types = traits
         super().__init__(**kwargs)
 
+    # NOTE: ``TraitType`` provides a default ``from_string`` that simply
+    # returns the string unchanged.  For a ``Union`` we need to attempt
+    # conversion with *each* contained trait until one succeeds.
+    # The previous implementation delegated to the first trait only,
+    # which caused wrong inference when the first trait could parse the
+    # string (or raised a generic ``TraitError``) but was not the
+    # appropriate type.
+    def from_string(self, s):
+        """
+        Parse a string ``s`` using the first sub‑trait that can successfully
+        convert it.
+
+        The method iterates over ``self.trait_types`` in order, calling each
+        sub‑trait's ``from_string``.  If a sub‑trait raises ``TraitError`` the
+        next one is tried.  If none succeed, a ``TraitError`` describing the
+        union is raised.
+        """
+        from .traitlets import TraitError  # local import to avoid circular deps
+
+        errors = []
+        for trait in self.trait_types:
+            # ``from_string`` may not be implemented for some traits; fall back
+            # to the generic ``TraitType.from_string`` which returns the raw
+            # string.  We still wrap the call in a try/except to capture
+            # conversion failures.
+            try:
+                # ``trait`` can be a ``TraitType`` instance or a class; ensure we
+                # have an instance with a ``from_string`` method.
+                if isinstance(trait, type):
+                    # instantiate with default kwargs (no metadata)
+                    trait_instance = trait()
+                else:
+                    trait_instance = trait
+
+                value = trait_instance.from_string(s)
+                # Validate the value to make sure it really conforms to the trait.
+                # ``validate`` will raise ``TraitError`` if the value is not
+                # acceptable for this sub‑trait.
+                return self.validate(None, value)
+            except TraitError as e:
+                errors.append(str(e))
+                continue
+
+        # If we reach here none of the sub‑traits could parse the string.
+        # Build a helpful error message that lists the allowed types.
+        allowed = ", ".join(
+            getattr(t, "info", lambda: t.__class__.__name__)()
+            if not isinstance(t, type)
+            else t.__name__
+            for t in self.trait_types
+        )
+        raise TraitError(
+            f"Unable to parse '{s}' as any of the Union types: {allowed}. "
+            f"Errors: {'; '.join(errors)}"
+        )
*** End of File
```

### What changed
1. Added a comprehensive `from_string` implementation for `Union`.
2. The method now:
   * Iterates over 

=== TASK fika-lang__fika-37 | repo fika-lang/fika | elixir
--- Issue
Allow Elixir-style string interpolation
Allow using `#{}` inside strings which will be expanded and replaced with the string it contains. The expression inside `#{}` should be of string type.
--- REAL FIX (ground truth)
diff --git a/lib/fika/erl_translate.ex b/lib/fika/erl_translate.ex
index 85b4a93..bb41552 100644
--- a/lib/fika/erl_translate.ex
+++ b/lib/fika/erl_translate.ex
@@ -94,10 +94,22 @@ defmodule Fika.ErlTranslate do
     {:var, line, name}
   end
 
-  defp translate_exp({:string, {line, _, _}, value}) do
+  defp translate_exp({:string, {line, _, _}, [value]}) when is_binary(value) do
     {:string, line, String.to_charlist(value)}
   end
 
+  defp translate_exp({:string, {line, _, _}, str_elements}) do
+    translated_exps =
+      str_elements
+      |> Enum.map(fn
+        value when is_binary(value) -> {:string, line, String.to_charlist(value)}
+        exp -> translate_exp(exp)
+      end)
+      |> Enum.map(&{:bin_element, line, &1, :default, :default})
+
+    {:bin, line, translated_exps}
+  end
+
   defp translate_exp({:list, {line, _, _}, value}) do
     do_translate_list(value, line)
   end
diff --git a/lib/fika/parser/helper.ex b/lib/fika/parser/helper.ex
index ccc6242..e4db4ef 100644
--- a/lib/fika/parser/helper.ex
+++ b/lib/fika/parser/helper.ex
@@ -104,7 +104,15 @@ defmodule Fika.Parser.Helper do
   end
 
   def do_to_ast({value, line}, :string) do
-    {:string, line, to_string(value)}
+    str =
+      value
+      |> Enum.chunk_by(&is_tuple/1)
+      |> Enum.flat_map(fn
+        [h | _tail] = interpolations when is_tuple(h) -> interpolations
+        charlist -> [to_string(charlist)]
+      end)
+
+    {:string, line, str}
   end
 
   def do_to_ast({result, line}, :exp_list) do
diff --git a/lib/fika/parser/literal_exps.ex b/lib/fika/parser/literal_exps.ex
index 21a01d1..90d6da8 100644
--- a/lib/fika/parser/literal_exps.ex
+++ b/lib/fika/parser/literal_exps.ex
@@ -22,9 +22,14 @@ defmodule Fika.Parser.LiteralExps do
     |> label("boolean")
     |> Helper.to_ast(:boolean)
 
+  interpolation =
+    ignore(string("\#{"))
+    |> concat(exp)
+    |> ignore(string("}"))
+
   string_exp =
     ignore(string("\""))
-    |> repeat(choice([string("\\\""), utf8_char(not: ?")]))
+    |> repeat(choice([interpolation, string("\\\""), utf8_char(not: ?")]))
     |> ignore(string("\""))
     |> Helper.to_ast(:string)
 
diff --git a/lib/fika/type_checker.ex b/lib/fika/type_checker.ex
index 5b704b3..9fc711a 100644
--- a/lib/fika/type_checker.ex
+++ b/lib/fika/type_checker.ex
@@ -152,9 +152,29 @@ defmodule Fika.TypeChecker do
   end
 
   # String
-  def infer_exp(env, {:string, _line, string}) do
-    Logger.debug("String #{string} found. Type: String")
-    {:ok, "String", env}
+  def infer_exp(env, {:string, _line, string_parts}) do
+    Enum.reduce_while(string_parts, env, fn
+      string, env when is_binary(string) ->
+        Logger.debug("String #{string} found. Type: String")
+        {:cont, {:ok, "String", env}}
+
+      exp, env ->
+        Logger.debug("String interpolation found. Inferring type of expression")
+
+        case infer_exp(env, exp) do
+          {:ok, "String", _env} = result ->
+            {:cont, result}
+
+          {:ok, o
--- Proposal A
## Root cause
The parser treats a double‑quoted literal as a single *string* token and creates a `String` AST node.  
Because the lexer never looks inside the literal, any `#{…}` sequence is kept as plain text, so the runtime never evaluates the expression inside the interpolation.  

## Fix
* Extend the AST with an `InterpolatedString` node that holds a list of parts (plain strings or expression ASTs).  
* Update the parser to detect `#{` inside a string literal, split the literal into literal parts and embedded expressions, parse each embedded expression, and build an `InterpolatedString` node.  
* Extend the evaluator to handle `InterpolatedString` by evaluating each part, ensuring embedded expressions evaluate to strings, and concatenating the results.

```diff
diff --git a/lib/fika/ast.ex b/lib/fika/ast.ex
--- a/lib/fika/ast.ex
+++ b/lib/fika/ast.ex
@@
   defmodule String do
     @enforce_keys [:value]
     defstruct [:value]
   end
+
+  # New node for strings that contain #{…} interpolations.
+  # `parts` is a list where each element is either a %String{} struct
+  # (plain text) or any other expression AST node (the interpolated part).
+  defmodule InterpolatedString do
+    @enforce_keys [:parts]
+    defstruct [:parts]
+  end
 end
```

```diff
diff --git a/lib/fika/parser.ex b/lib/fika/parser.ex
--- a/lib/fika/parser.ex
+++ b/lib/fika/parser.ex
@@
   defp parse_primary(%{type: :string, value: raw} = token, state) do
-    {%String{value: raw}, state}
+    # If the raw string contains an interpolation marker, build an
+    # InterpolatedString node; otherwise keep the plain String node.
+    if String.contains?(raw, "#{") do
+      {parts, rest_state} = parse_interpolated_string(raw, state)
+      {%InterpolatedString{parts: parts}, rest_state}
+    else
+      {%String{value: raw}, state}
+    end
   end
+
+  # ----------------------------------------------------------------------
+  # Interpolation handling
+  # ----------------------------------------------------------------------
+  # Walk through the raw string, split it into literal fragments and
+  # embedded expressions delimited by #{ … }.
+  # Returns {parts, state} where `parts` is a list of AST nodes.
+  defp parse_interpolated_string(raw, state) do
+    # Helper to find the matching closing brace for an opening '#{'.
+    find_matching_brace = fn str ->
+      {expr, rest} =
+        str
+        |> String.graphemes()
+        |> Enum.reduce_while({[], 0}, fn
+          "}", {acc, 1} -> {:halt, {Enum.reverse(acc) |> Enum.join(), ""}}
+          "}", {acc, depth} -> {:cont, {[} | acc], depth - 1}
+          "{", {acc, depth} -> {:cont, {"{" <> acc, depth + 1}}
+          ch, {acc, depth} -> {:cont, {[ch | acc], depth}}
+        end)
+
+      # `expr` is the characters inside the braces, `rest` is the remaining
+      # string after the closing brace.
+      {expr, rest}
+    end
+
+    parts = []
+    remaining = raw
+    cur_state = state
+
+    while String.length(remaining) > 0 do
+      case String.split(remaining, "#{", parts: 2) do
+        [before, after] ->
+          # Add the literal part before the interpolation (if any)
+          parts = if before != "" do
+            parts ++ [%String{value: before}]
+          else
+            parts
+          end
+
+          # Extract the expression up to the matching '}'
+          {expr_str, after_rest} = find_matching_brace.(after)
+
+          # Parse the extracted expression using the existing expression parser
--- Proposal B
## Root cause
`Fika` treats string literals as plain text and never looks inside them.  
Therefore a literal such as `"Hello #{name}"` is stored and later returned
exactly as‑is, without evaluating the expression inside `#{}`.  
The language needs a step that, **when a string literal is evaluated**, parses
the interpolated fragments, evaluates each fragment as a normal expression,
checks that the result is a string, and concatenates everything back together.

## Fix
Add interpolation handling to the evaluator.  
The fix:

* Detect a string literal that contains `#{`.
* Split the literal into literal parts and the code inside each `#{…}`.
* Parse each embedded fragment with the existing parser.
* Evaluate the fragment in the current environment.
* Verify the fragment evaluates to a string; otherwise raise a clear error.
* Concatenate the pieces and return the final string.

The changes are made in **`lib/fika/evaluator.ex`** (the module that evaluates
AST nodes).  The rest of the compiler (parser, type‑checker) can stay unchanged
because interpolation is performed at run‑time.

```diff
--- a/lib/fika/evaluator.ex
+++ b/lib/fika/evaluator.e
@@
-defmodule Fika.Evaluator do
+defmodule Fika.Evaluator do
   alias Fika.{Parser, Env}
   require Logger
 
   @type env :: Env.t()
   @type eval_result :: {:ok, any()} | {:error, String.t()}
@@
   # ---------------------------------------------------------------------------
   # Public API
   # ---------------------------------------------------------------------------
 
   @spec eval(ast :: any(), env :: env()) :: eval_result()
   def eval(ast, env \\ Env.new())
   def eval({:int, value}, _env), do: {:ok, value}
-  def eval({:string, value}, _env), do: {:ok, value}
+  # String literals may contain interpolation (`#{…}`) which must be evaluated.
+  def eval({:string, raw}, env) do
+    case interpolate_string(raw, env) do
+      {:ok, result} -> {:ok, result}
+      {:error, _} = err -> err
+    end
+  end
   def eval({:bool, value}, _env), do: {:ok, value}
   # … other eval clauses …
 
   # ---------------------------------------------------------------------------
   # Helpers
   # ---------------------------------------------------------------------------
 
+  @doc """
+  Handles Elixir‑style string interpolation.
+
+  The function walks through the raw string, finds every `#{expr}` fragment,
+  parses `expr` using the normal parser, evaluates it in the given environment,
+  ensures the result is a binary, and finally concatenates all parts.
+  """
+  @spec interpolate_string(String.t(), env()) :: {:ok, String.t()} | {:error, String.t()}
+  defp interpolate_string(str, env) do
+    # Fast‑path: no interpolation at all.
+    if not String.contains?(str, "#{") do
+      {:ok, str}
+    else
+      do_interpolate(str, env, "")
+    end
+  end
+
+  # Recursive implementation that consumes the string left‑to‑right.
+  defp do_interpolate(<<>>, _env, acc), do: {:ok, acc}
+
+  defp do_interpolate(<<rest::binary>>, env, acc) do
+    # Look for the next interpolation start.
+    case String.split(rest, "#{" , parts: 2) do
+      [before, after] ->
+        # `after` now starts with the expression and the closing `}`.
+        case split_until_closing_brace(after) do
+          {:ok, expr_str, remaining} ->
+            with {:ok, expr_ast} <- Parser.parse(expr_str),
+                 {:ok, expr_val} <- eval(expr_ast, env),
+                 true <- is_binary(expr_val) do
+              new_acc = acc <> bef

=== TASK corteva__rioxarray-14 | repo corteva/rioxarray | python
--- Issue
Add ability to set CRS and write CRS information to dataset
See: https://github.com/geoxarray/geoxarray/issues/9
--- REAL FIX (ground truth)
diff --git a/rioxarray/exceptions.py b/rioxarray/exceptions.py
index 723a391..99a58fd 100644
--- a/rioxarray/exceptions.py
+++ b/rioxarray/exceptions.py
@@ -26,3 +26,7 @@ class TooManyDimensions(RioXarrayError):
 
 class InvalidDimensionOrder(RioXarrayError):
     """This is raised when there the dimensions are not ordered correctly."""
+
+
+class MissingCRS(RioXarrayError):
+    """Missing the CRS in the dataset."""
diff --git a/rioxarray/rioxarray.py b/rioxarray/rioxarray.py
index acefb4b..1a9b0ec 100644
--- a/rioxarray/rioxarray.py
+++ b/rioxarray/rioxarray.py
@@ -25,6 +25,7 @@ from scipy.interpolate import griddata
 
 from rioxarray.exceptions import (
     InvalidDimensionOrder,
+    MissingCRS,
     NoDataInBounds,
     OneDimensionalRaster,
     TooManyDimensions,
@@ -32,7 +33,7 @@ from rioxarray.exceptions import (
 from rioxarray.crs import crs_to_wkt
 
 FILL_VALUE_NAMES = ("_FillValue", "missing_value", "fill_value", "nodata")
-UNWANTED_RIO_ATTRS = ("nodata", "nodatavals", "crs", "is_tiled", "res")
+UNWANTED_RIO_ATTRS = ("nodatavals", "crs", "is_tiled", "res")
 DEFAULT_GRID_MAP = "spatial_ref"
 
 
@@ -123,23 +124,9 @@ def add_xy_grid_meta(coords):
 
 
 def add_spatial_ref(in_ds, dst_crs, grid_map_name):
-    # remove old grid map if exists
-    try:
-        del in_ds.coords[grid_map_name]
-    except KeyError:
-        pass
-
-    # add grid mapping variable
-    in_ds.coords[grid_map_name] = xarray.Variable((), 0)
-    match_proj = crs_to_wkt(CRS.from_user_input(dst_crs))
-
-    grid_map_attrs = dict()
-    # add grid mapping variable
-    grid_map_attrs["spatial_ref"] = match_proj
-    # http://cfconventions.org/Data/cf-conventions/cf-conventions-1.7/cf-conventions.html#appendix-grid-mappings
-    # http://desktop.arcgis.com/en/arcmap/10.3/manage-data/netcdf/spatial-reference-for-netcdf-data.htm
-    grid_map_attrs["crs_wkt"] = match_proj
-    in_ds.coords[grid_map_name].attrs = grid_map_attrs
+    in_ds.rio.write_crs(
+        input_crs=dst_crs, grid_mapping_name=grid_map_name, inplace=True
+    )
     return in_ds
 
 
@@ -230,7 +217,93 @@ class XRasterBase(object):
         """:obj:`rasterio.crs.CRS`:
             The projection of the dataset.
         """
-        return self._crs
+        raise NotImplementedError
+
+    def set_crs(self, input_crs, inplace=True):
+        """
+        Set the CRS value for the Dataset/DataArray without modifying
+        the dataset/data array.
+
+        Parameters:
+        -----------
+        input_crs: object
+            Anythong accepted by `rasterio.crs.CRS.from_user_input`.
+
+        Returns:
+        --------
+        xarray.Dataset or xarray.DataArray:
+        Dataset with crs attribute.
+
+        """
+        crs = CRS.from_user_input(input_crs)
+        if inplace:
+            self._crs = crs
+            return self._obj
+        xobj = self._obj.copy(deep=True)
+        xobj.rio._crs = crs
+        return xobj
+
+    def write_crs(
+        self, input_crs=None, grid_mapping_
--- Proposal A
**## Root cause**  
`rioxarray` only provided a `set_crs` method on the `DataArray` accessor and it silently mutated the CRS metadata without warning. The accessor for `Dataset` had no way to assign a CRS, and there was no clear separation between the *lossy* in‑memory CRS setter and the *safe* method that writes CRS information to the underlying raster metadata. This made it impossible for users to set a CRS on a whole `xarray.Dataset` and to persist that CRS when exporting the dataset.

**## Fix (a unified diff with your best‑guess file paths and code)**  

```diff
--- a/rioxarray/rioxarray_accessor.py
+++ b/rioxarray/rioxarray_accessor.py
@@
 from typing import Optional, Union, Tuple, List, Dict
 import warnings
+import rasterio
+from rasterio.crs import CRS
@@
 class RasterArray(xr.core.common.Dataset):
@@
 class RasterArrayAccessor:
@@
-    def set_crs(self, crs: Union[CRS, str, dict], inplace: bool = False):
-        """
-        Set the CRS of the DataArray. This operation is *lossy* – it only
-        updates the in‑memory metadata and does **not** write the CRS to the
-        underlying raster file.
-
-        Parameters
-        ----------
-        crs : rasterio.crs.CRS, str, or dict
-            The CRS to assign.
-        inplace : bool, default False
-            If True, modify the DataArray in place and return None.
-
-        Returns
-        -------
-        xr.DataArray
-            A copy of the DataArray with the new CRS (unless ``inplace=True``).
-        """
-        warnings.warn(
-            "The `set_crs` method is deprecated and will be removed in a future "
-            "release. Use `write_crs` instead, which writes the CRS to the "
-            "underlying raster file.",
-            FutureWarning,
-            stacklevel=2,
-        )
-        return self._set_crs(crs, inplace=inplace)
-
-    def _set_crs(self, crs: Union[CRS, str, dict], inplace: bool = False):
-        """
-        Internal helper that actually sets the CRS on the DataArray without
-        emitting a deprecation warning.
-        """
-        crs_obj = CRS.from_user_input(crs)
-        if inplace:
-            self._obj.rio._crs = crs_obj
-            return None
-        else:
-            new = self._obj.copy()
-            new.rio._crs = crs_obj
-            return new
+    # --------------------------------------------------------------------- #
+    # CRS handling – public API
+    # --------------------------------------------------------------------- #
+    def set_crs(self, crs: Union[CRS, str, dict], inplace: bool = False):
+        """
+        **Deprecated** – set the CRS in‑memory only.
+
+        This method mutates the CRS metadata of the object but does **not**
+        write the CRS to any underlying raster file.  It is kept for backward
+        compatibility and will be removed in a future release.  New code should
+        use :meth:`write_crs` which persists the CRS to the raster on disk.
+
+        Parameters
+        ----------
+        crs : rasterio.crs.CRS, str, or dict
+            The CRS to assign.
+        inplace : bool, default False
+            If True, modify the object in place and return ``None``.
+
+        Returns
+        -------
+        xr.DataArray or None
+            A copy of the object with the new CRS (unless ``inplace=True``).
+        """
+        warnings.warn(
+            "The `set_crs` method is deprecated and will be removed in a future "
+            "release. Use `write_crs` inst
--- Proposal B
## Root cause
`rioxarray` only implements CRS handling for `xarray.DataArray` objects. The accessor (`.rio`) does not expose a `set_crs` / `write_crs` method for `xarray.Dataset`, so attempts to set or persist a CRS on a `Dataset` silently fail or raise an `AttributeError`.  The missing implementation means that CRS information cannot be attached to a `Dataset` nor written to disk when the dataset is saved (e.g., to NetCDF or GeoTIFF).

## Fix
Add CRS handling to the `Dataset` accessor by implementing `set_crs` and `write_crs` methods that:

* Accept a `rasterio.crs.CRS` object, EPSG code, PROJ string, or dict.
* Store the CRS in the dataset’s attributes (`crs_wkt` and `spatial_ref` – the same keys used by rasterio).
* Optionally propagate the CRS to all data variables that contain spatial dimensions.
* Respect the `inplace` flag (default `False`) to either modify the original object or return a copy.
* Ensure that the CRS is also written when the dataset is exported via `rio.to_raster` or `rio.to_netcdf`.

Below is a **unified diff** that adds the required functionality to `rioxarray/rioxarray_accessor.py`.  The diff assumes the existing accessor already defines methods for `DataArray`; we extend the `Dataset` branch accordingly.

```diff
--- a/rioxarray/rioxarray_accessor.py
+++ b/rioxarray/rioxarray_accessor.py
@@
 class RasterioAccessor:
@@
     def __init__(self, xarray_obj):
         self._obj = xarray_obj
         self._validate()
 
+    # -----------------------------------------------------------------
+    # Helper utilities (shared between DataArray and Dataset)
+    # -----------------------------------------------------------------
+    @staticmethod
+    def _parse_crs(crs):
+        """
+        Convert various CRS specifications to a rasterio CRS object.
+        """
+        import rasterio.crs
+        if isinstance(crs, rasterio.crs.CRS):
+            return crs
+        # Accept EPSG integer, PROJ string, dict, etc.
+        return rasterio.crs.CRS.from_user_input(crs)
+
+    @staticmethod
+    def _crs_attrs(crs):
+        """
+        Return a dict of attributes that rasterio expects for CRS storage.
+        """
+        # rasterio stores CRS as WKT in the attribute ``crs_wkt`` and also
+        # provides a ``spatial_ref`` attribute for compatibility with GDAL.
+        return {
+            "crs_wkt": crs.to_wkt(),
+            "spatial_ref": crs.to_wkt(),
+        }
+
+    # -----------------------------------------------------------------
+    # CRS handling for DataArray (existing implementation – unchanged)
+    # -----------------------------------------------------------------
@@
     def crs(self):
         """Return the CRS of the DataArray."""
         return self._obj.rio._crs
@@
     def set_crs(self, crs, inplace=False):
         """
         Set the CRS on the DataArray without writing to disk.
@@
         self._obj.rio._crs = crs_obj
         if not inplace:
             return self._obj.copy()
         return self._obj
+
+    # -----------------------------------------------------------------
+    # New: CRS handling for Dataset
+    # -----------------------------------------------------------------
+    @property
+    def crs(self):
+        """
+        Return the CRS stored in the Dataset attributes.
+        If the attribute is missing, ``None`` is returned.
+        """
+        # Prefer the explicit ``crs_wkt`` attribute; fall back to ``spatial_ref``.
+        wkt = self._obj.attrs.get("crs

=== TASK agnostiqhq__covalent-1251 | repo AgnostiqHQ/covalent | python
--- Issue
Corrupted config file
### Environment

- **Covalent version**: 0.198.0
- **Python version**: 3.8.13
- **Operating system**: MacOS 12.3.1


### What is happening?

It seems the corrupted config file issue has crept up again. This is most likely due to performing multiple writes from different threads.

### How can we reproduce the issue?

After installing covalent, when I do

```bash
covalent start --ignore-migrations -d
covalent purge -y
```

Then the following error is thrown:
```bash
toml.decoder.TomlDecodeError: Found invalid character in key name: ']'. Try quoting the key name. (line 33 column 16 char 927)
```

This happens even when say a test is run after starting covalent server. So it is rather sporadic, and might be difficult to reproduce.

### What should happen?

No error should be thrown.

### Any suggestions?

I'd say the solution for this would be to not perform any writes whenever the `ConfigManager` is initialized. Instead do it when necessary. Reading however can still be performed.
--- REAL FIX (ground truth)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 7c2770fa..0f94bd8c 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -11,6 +11,10 @@ and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0
 
 - `requirements-client.txt` file added.
 
+### Fixed
+
+- Config file is now locked during reads and writes to mitigate concurrency issues
+
 ### Changed
 
 - Installation requirements are now split into client side and server side requirements' files.
diff --git a/covalent/_shared_files/config.py b/covalent/_shared_files/config.py
index 47523d52..89eb82e9 100644
--- a/covalent/_shared_files/config.py
+++ b/covalent/_shared_files/config.py
@@ -18,6 +18,7 @@
 #
 # Relief from the License may be granted by purchasing a commercial license.
 
+import fcntl
 import os
 import shutil
 from dataclasses import asdict
@@ -47,10 +48,13 @@ class ConfigManager:
         self.config_file = DEFAULT_CONFIG["sdk"]["config_file"]
 
         self.generate_default_config()
-        if os.path.exists(self.config_file):
-            # Update config with user configuration file:
+
+        try:
+            with open(self.config_file, "r") as f:
+                pass
+
             self.update_config()
-        else:
+        except FileNotFoundError:
             Path(self.config_file).parent.mkdir(parents=True, exist_ok=True)
             self.write_config()
 
@@ -110,14 +114,16 @@ class ConfigManager:
                         else:
                             old_dict.setdefault(key, value)
 
-        if os.path.exists(self.config_file):
-            file_config = toml.load(self.config_file)
+        with open(self.config_file, "r+") as f:
+            fcntl.lockf(f, fcntl.LOCK_EX)
+            file_config = toml.load(f)
+
             update_nested_dict(self.config_data, file_config)
             if new_entries:
                 update_nested_dict(self.config_data, new_entries, override_existing)
 
-        # Writing it back to the file
-        self.write_config()
+            # Writing it back to the file
+            self.write_config()
 
     def read_config(self) -> None:
         """
@@ -142,8 +148,8 @@ class ConfigManager:
         Returns:
             None
         """
-
         with open(self.config_file, "w") as f:
+            fcntl.lockf(f, fcntl.LOCK_EX)
             toml.dump(self.config_data, f)
 
     def purge_config(self) -> None:

--- Proposal A
## Root cause
`ConfigManager` writes the configuration file every time it is instantiated.  
When several threads (or processes) start the Covalent server simultaneously they each create a `ConfigManager` instance, causing concurrent writes to the same TOML file. The interleaved writes corrupt the file (e.g., stray `]` characters), which later triggers a `toml.decoder.TomlDecodeError` when the file is read.

## Fix
* Stop writing the configuration file on `ConfigManager` construction.  
* Perform writes only when a configuration value is changed via `set_config`.  
* Guard the write operation with a process‑wide `threading.Lock` to make the write atomic if multiple threads attempt to modify the config at the same time.  

```diff
--- a/covalent/_config/config_manager.py
+++ b/covalent/_config/config_manager.py
@@
-import os
-import threading
-import toml
-from pathlib import Path
-
-from .defaults import DEFAULT_CONFIG
-
-_write_lock = threading.Lock()
-
-
-class ConfigManager:
-    """
-    Handles reading and writing of the Covalent configuration file.
-    """
-
-    def __init__(self):
-        self.config_path = Path(os.getenv("COVALENT_CONFIG_PATH", "~/.covalent/config.toml")).expanduser()
-        self._ensure_config_dir()
-        # Load existing config or create a new one with defaults
-        if self.config_path.is_file():
-            self._config = self._read_config()
-        else:
-            self._config = DEFAULT_CONFIG.copy()
-            self._write_config()   # <-- problematic write on init
-
-    def _ensure_config_dir(self):
-        self.config_path.parent.mkdir(parents=True, exist_ok=True)
-
-    def _read_config(self):
-        with self.config_path.open("r") as f:
-            return toml.load(f)
-
-    def _write_config(self):
-        # Serialize and write the config atomically
-        with _write_lock:
-            tmp_path = self.config_path.with_suffix(".tmp")
-            with tmp_path.open("w") as f:
-                toml.dump(self._config, f)
-            tmp_path.replace(self.config_path)
-
-    def get_config(self, key):
-        """Retrieve a configuration value."""
-        return self._config.get(key, None)
-
-    def set_config(self, key, value):
-        """Set a configuration value and persist it."""
-        self._config[key] = value
-        self._write_config()
+import os
+import threading
+import toml
+from pathlib import Path
+
+from .defaults import DEFAULT_CONFIG
+
+# A module‑level lock that serialises writes to the config file across
+# all threads/processes that import this module.
+_write_lock = threading.Lock()
+
+
+class ConfigManager:
+    """
+    Handles reading and writing of the Covalent configuration file.
+
+    The manager **does not** write the configuration file on construction.
+    Writes are performed only when ``set_config`` is called.  This prevents
+    race conditions when multiple threads instantiate ``ConfigManager`` at
+    the same time (e.g., during server start‑up).
+    """
+
+    def __init__(self):
+        # Resolve the location of the user configuration file.
+        self.config_path = Path(
+            os.getenv("COVALENT_CONFIG_PATH", "~/.covalent/config.toml")
+        ).expanduser()
+        self._ensure_config_dir()
+
+        # Load the existing configuration if the file is present.
+        # If the file does not exist we keep the defaults **in memory** only;
+        # we defer writing them until the user explicitly changes a value.
+        i
--- Proposal B
## Root cause
`ConfigManager` writes the configuration file every time it is instantiated.  
When the Covalent server starts it creates a `ConfigManager` in several
different threads (e.g. the main server thread, the dispatcher thread,
the UI thread, etc.). Because the writes are not synchronized, two threads
can interleave their `open(..., "w")` calls and truncate the file while the
other thread is still writing. The resulting file contains a mixture of
partial TOML fragments, which leads to the `TomlDecodeError` that mentions an
invalid character (`]`) in a key name.

## Fix
* Stop writing the configuration file automatically on initialization – only
  write when the configuration is actually changed.
* Guard all write operations with a process‑wide `threading.Lock` so that
  concurrent writes are serialized.
* Keep the existing public API (`save()`, `update()`, etc.) unchanged.

```diff
--- a/covalent/_config/config_manager.py
+++ b/covalent/_config/config_manager.py
@@
-import os
-import toml
-from pathlib import Path
-from typing import Any, Dict
+import os
+import toml
+import threading
+from pathlib import Path
+from typing import Any, Dict
@@
-class ConfigManager:
-    """
-    Handles reading and writing the Covalent configuration file.
-    """
-
-    _instance = None
-
-    def __new__(cls, *args, **kwargs):
-        if cls._instance is None:
-            cls._instance = super(ConfigManager, cls).__new__(cls)
-        return cls._instance
-
-    def __init__(self, config_path: str = None):
-        self.config_path = Path(config_path or os.getenv("COVALENT_CONFIG", "~/.covalent/config.toml")).expanduser()
-        self._config = {}
-        self._load()
-        # Write the default config on first init so that a file always exists.
-        # This write is the source of race conditions when multiple threads
-        # instantiate ConfigManager simultaneously.
-        self._write()
-
-    def _load(self) -> None:
-        """Load configuration from the TOML file."""
-        if self.config_path.is_file():
-            with open(self.config_path, "r", encoding="utf-8") as f:
-                self._config = toml.load(f)
-        else:
-            self._config = {}
-
-    def _write(self) -> None:
-        """Write the current configuration to the TOML file."""
-        os.makedirs(self.config_path.parent, exist_ok=True)
-        with open(self.config_path, "w", encoding="utf-8") as f:
-            toml.dump(self._config, f)
-
-    def get(self, key: str, default: Any = None) -> Any:
-        return self._config.get(key, default)
-
-    def set(self, key: str, value: Any) -> None:
-        self._config[key] = value
-        self._write()
-
-    def update(self, updates: Dict[str, Any]) -> None:
-        self._config.update(updates)
-        self._write()
-
-    def as_dict(self) -> Dict[str, Any]:
-        return dict(self._config)
+class ConfigManager:
+    """
+    Handles reading and writing the Covalent configuration file.
+
+    The manager is a singleton – all parts of the application share the same
+    instance.  Writes are now **lazy** (performed only when the configuration
+    is mutated) and are protected by a thread‑level lock to avoid race
+    conditions that previously corrupted the TOML file.
+    """
+
+    _instance = None
+    # A lock that serialises all writes to the config file across threads.
+    _write_lock = threading.Lock()
+
+    def __new__(cls, *args, **kwargs):
+        if cls._instance is None:


=== TASK casey__just-2460 | repo casey/just | rust
--- Issue
Command line flag to exit without error if recipe not found
In some cases it's helpful to run `just` so that it silently exists with code 0 if requested recipe is not found.  Possible usecase: running same recipe in multiple directories (e.g. with GNU parallel) when in some of them the recipe is not defined, in which case we just want to skip the dir.

It is currently possible to do with NPM scripts, for example `npm run --ws --if-present <script>` will run *script* in every workspace where script is defined. 
--- REAL FIX (ground truth)
diff --git a/src/config.rs b/src/config.rs
index d0b91d67..6eb8d23b 100644
--- a/src/config.rs
+++ b/src/config.rs
@@ -9,6 +9,7 @@ use {
 
 #[derive(Debug, PartialEq)]
 pub(crate) struct Config {
+  pub(crate) allow_missing: bool,
   pub(crate) check: bool,
   pub(crate) color: Color,
   pub(crate) command_color: Option<ansi_term::Color>,
@@ -80,6 +81,7 @@ mod cmd {
 }
 
 mod arg {
+  pub(crate) const ALLOW_MISSING: &str = "ALLOW-MISSING";
   pub(crate) const ARGUMENTS: &str = "ARGUMENTS";
   pub(crate) const CHECK: &str = "CHECK";
   pub(crate) const CHOOSER: &str = "CHOOSER";
@@ -315,6 +317,13 @@ impl Config {
           .help("Suppress all output")
           .conflicts_with(arg::DRY_RUN),
       )
+      .arg(
+        Arg::new(arg::ALLOW_MISSING)
+          .long("allow-missing")
+          .env("JUST_ALLOW_MISSING")
+          .action(ArgAction::SetTrue)
+          .help("Ignore missing recipe and module errors"),
+      )
       .arg(
         Arg::new(arg::SET)
           .long("set")
@@ -706,6 +715,7 @@ impl Config {
     let explain = matches.get_flag(arg::EXPLAIN);
 
     Ok(Self {
+      allow_missing: matches.get_flag(arg::ALLOW_MISSING),
       check: matches.get_flag(arg::CHECK),
       color: (*matches.get_one::<UseColor>(arg::COLOR).unwrap()).into(),
       command_color: matches
diff --git a/src/error.rs b/src/error.rs
index dd6955fe..72438048 100644
--- a/src/error.rs
+++ b/src/error.rs
@@ -468,7 +468,7 @@ impl<'src> ColorDisplay for Error<'src> {
         write!(f, "{count} {overrides} overridden on the command line but not present in justfile")?;
       }
       UnknownRecipe { recipe, suggestion } => {
-        write!(f, "Justfile does not contain recipe `{recipe}`.")?;
+        write!(f, "Justfile does not contain recipe `{recipe}`")?;
         if let Some(suggestion) = suggestion {
           write!(f, "\n{suggestion}")?;
         }
diff --git a/src/subcommand.rs b/src/subcommand.rs
index 848bd59f..cc47d879 100644
--- a/src/subcommand.rs
+++ b/src/subcommand.rs
@@ -145,6 +145,15 @@ impl Subcommand {
         }
       }
 
+      if config.allow_missing
+        && matches!(
+          result,
+          Err(Error::UnknownRecipe { .. } | Error::UnknownSubmodule { .. })
+        )
+      {
+        return Ok(());
+      }
+
       return result;
     }
   }

--- Proposal A
## Root cause
`just` treats a missing recipe as a fatal error and exits with a non‑zero status. There is no option to change this behaviour, so scripts that invoke `just` in many directories (e.g. via GNU parallel) cannot silently skip directories where the recipe does not exist.

## Fix (a unified diff with your best‑guess file paths and code)
```diff
--- a/src/cli.rs
+++ b/src/cli.rs
@@
 #[derive(Parser, Debug)]
 #[command(author, version, about, long_about = None)]
 pub struct Options {
@@
     /// Number of jobs to run in parallel (default: number of CPUs)
     #[arg(short = 'j', long = "jobs")]
     pub jobs: Option<usize>,
+
+    /// Exit with status 0 instead of an error when the requested recipe is not found.
+    ///
+    /// This mirrors the behaviour of tools like `npm run --if-present`.  When the
+    /// flag is supplied and the named recipe does not exist, `just` will terminate
+    /// silently with exit code 0, allowing callers to ignore missing recipes.
+    #[arg(long = "if-present", hide = false)]
+    pub if_present: bool,
@@
     /// The name of the recipe to run.
     #[arg(value_name = "RECIPE")]
     pub recipe: Option<String>,
```

```diff
--- a/src/main.rs
+++ b/src/main.rs
@@
 fn main() -> Result<()> {
@@
-    let opts = Options::parse();
+    let opts = Options::parse();
@@
-    // Resolve the recipe name (or use the default)
-    let recipe_name = match opts.recipe {
-        Some(ref name) => name.clone(),
-        None => {
-            // If no recipe was given, just run the default recipe (if any)
-            // This logic lives in Justfile::default_recipe()
-            match justfile.default_recipe_name() {
-                Some(name) => name.clone(),
-                None => {
-                    eprintln!("error: no recipe specified and no default recipe defined");
-                    std::process::exit(1);
-                }
-            }
-        }
-    };
-
-    // Look up the recipe; this may fail if the name does not exist.
-    let recipe = match justfile.get_recipe(&recipe_name) {
-        Ok(r) => r,
-        Err(e) => {
-            eprintln!("error: {}", e);
-            std::process::exit(1);
-        }
-    };
+    // Resolve the recipe name (or use the default)
+    let recipe_name = match opts.recipe {
+        Some(ref name) => name.clone(),
+        None => {
+            // If no recipe was given, just run the default recipe (if any)
+            // This logic lives in Justfile::default_recipe_name()
+            match justfile.default_recipe_name() {
+                Some(name) => name.clone(),
+                None => {
+                    eprintln!("error: no recipe specified and no default recipe defined");
+                    std::process::exit(1);
+                }
+            }
+        }
+    };
+
+    // Look up the recipe; this may fail if the name does not exist.
+    let recipe = match justfile.get_recipe(&recipe_name) {
+        Ok(r) => r,
+        Err(e) => {
+            // New behaviour: if `--if-present` is set, treat a missing recipe as a
+            // successful no‑op instead of an error.
+            if opts.if_present {
+                std::process::exit(0);
+            } else {
+                eprintln!("error: {}", e);
+                std::process::exit(1);
+            }
+        }
+    };
*** End of File
```

## How to verify
1. **Build the project**  
   ```bash
   cargo build --release
   ```

2. **Run a known‑missing recipe without the flag*
--- Proposal B
**## Root cause**

`just` treats a missing recipe as a hard error and exits with status 1.  
There is no way for a user to ask the program to ignore a missing recipe and
return success (status 0).  The CLI parser therefore never records a flag for
this behaviour and the execution path that looks up a recipe unconditionally
calls `error!()` when the lookup fails.

**## Fix (a unified diff with your best‑guess file paths and code)**

```diff
--- a/src/cli.rs
+++ b/src/cli.rs
@@
 #[derive(Parser, Debug)]
 pub struct Cli {
@@
     /// Name of the recipe to run
     #[arg(value_name = "RECIPE")]
     pub recipe: Option<String>,
+
+    /// Exit with status 0 instead of an error when the requested recipe is not
+    /// present.  This mirrors the behaviour of tools such as `npm run --if-present`.
+    ///
+    /// When this flag is supplied and the named recipe cannot be found, `just`
+    /// will silently succeed (exit code 0) rather than printing an error and
+    /// exiting with code 1.
+    #[arg(
+        long = "if-present",
+        help = "Exit with status 0 if the requested recipe does not exist"
+    )]
+    pub if_present: bool,
```

```diff
--- a/src/main.rs
+++ b/src/main.rs
@@
 fn main() -> anyhow::Result<()> {
@@
-    let cli = Cli::parse();
+    let cli = Cli::parse();
@@
-    // Resolve the Justfile (or Justfile.toml) in the current directory or any
-    // parent directory.
-    let justfile = Justfile::load(&cli)?;
-
-    // If a recipe name was supplied, try to run it.
-    if let Some(recipe_name) = cli.recipe {
-        let recipe = justfile
-            .recipe(&recipe_name)
-            .ok_or_else(|| anyhow!("recipe `{}` not found", recipe_name))?;
-        run_recipe(&justfile, recipe, &cli)?;
-        return Ok(());
-    }
-
-    // No recipe name supplied – run the default recipe (if any) or list the
-    // available recipes.
-    if let Some(default) = justfile.default_recipe() {
-        run_recipe(&justfile, default, &cli)?;
-    } else {
-        justfile.print_recipes();
-    }
-
-    Ok(())
+    // Resolve the Justfile (or Justfile.toml) in the current directory or any
+    // parent directory.
+    let justfile = Justfile::load(&cli)?;
+
+    // -----------------------------------------------------------------------
+    // 1️⃣  Run a specific recipe if the user asked for one.
+    // -----------------------------------------------------------------------
+    if let Some(recipe_name) = cli.recipe {
+        match justfile.recipe(&recipe_name) {
+            Some(recipe) => {
+                // Normal path – the recipe exists, execute it.
+                run_recipe(&justfile, recipe, &cli)?;
+                return Ok(());
+            }
+            None => {
+                // The recipe does not exist.
+                if cli.if_present {
+                    // The new flag tells us to treat this as a non‑error.
+                    // Silently succeed with exit status 0.
+                    std::process::exit(0);
+                } else {
+                    // Preserve the original behaviour for callers that did not
+                    // request the tolerant mode.
+                    anyhow::bail!("recipe `{}` not found", recipe_name);
+                }
+            }
+        }
+    }
+
+    // -----------------------------------------------------------------------
+    // 2️⃣  No explicit recipe – run the default recipe (if any) or list recipes.
+    // ------------------------------------

=== TASK nipy__nipype-3248 | repo nipy/nipype | python
--- Issue
Mrtrix3's dwibiascorrect interface crashes due to "-"
Interface crashes because of "-" before algorithm specification.

```python
RuntimeError: Command:
```
```bash
dwibiascorrect -mask <mask_file> -ants <in_file> <out_file>

Standard output:

Standard error:

Error: argument algorithm: invalid choice: <in_file> (choose from 'ants', 'fsl')
Usage: dwibiascorrect algorithm [ options ] ...
       (Run dwibiascorrect -help for more information)

Return code: 1
```
The expected behavior would be to execute the following command:
```bash
dwibiascorrect ants [options] <in_file> <out_file>
```
To replicate the behavior, simply execute:
```python
import nipype.interface.mrtrix3 as mrt
bc = mrt.DWIBiasCorrect()
bc.inputs.in_file = <in_file>
bc.inputs.out_file = <out_file>
bc.inputs.use_ants = True
bc.run()
```
Running on a 64 bit Linux machine (Ubuntu)

```
{'commit_hash': '72945ef34',
 'commit_source': 'installation',
 'networkx_version': '2.4',
 'nibabel_version': '3.0.0',
 'nipype_version': '1.5.0',
 'numpy_version': '1.19.0',
 'pkg_path': '/home/groot/Projects/labbing/venv/lib/python3.7/site-packages/nipype',
 'scipy_version': '1.5.0',
 'sys_executable': '/home/groot/Projects/labbing/venv/bin/python',
 'sys_platform': 'linux',
 'sys_version': '3.7.8 (default, Jun 29 2020, 04:26:04) \n[GCC 9.3.0]',
 'traits_version': '6.1.1'}
```
- My python environment outside container

--- REAL FIX (ground truth)
diff --git a/.zenodo.json b/.zenodo.json
index cb9ffacc3..a8ae4c70e 100644
--- a/.zenodo.json
+++ b/.zenodo.json
@@ -697,6 +697,10 @@
       "affiliation": "Sagol School of Neuroscience, Tel Aviv University",
       "name": "Baratz, Zvi"
     },
+    {
+      "affiliation": "Sagol School of Neuroscience, Tel Aviv University",
+      "name": "Ben-Zvi, Gal"
+    },
     {
       "name": "Matsubara, K"
     },
diff --git a/nipype/interfaces/mrtrix3/preprocess.py b/nipype/interfaces/mrtrix3/preprocess.py
index 9384ef43c..aa3347c7f 100644
--- a/nipype/interfaces/mrtrix3/preprocess.py
+++ b/nipype/interfaces/mrtrix3/preprocess.py
@@ -181,15 +181,17 @@ class DWIBiasCorrectInputSpec(MRTrix3BaseInputSpec):
     )
     in_mask = File(argstr="-mask %s", desc="input mask image for bias field estimation")
     use_ants = traits.Bool(
-        argstr="-ants",
+        argstr="ants",
         mandatory=True,
         desc="use ANTS N4 to estimate the inhomogeneity field",
+        position=0,
         xor=["use_fsl"],
     )
     use_fsl = traits.Bool(
-        argstr="-fsl",
+        argstr="fsl",
         mandatory=True,
         desc="use FSL FAST to estimate the inhomogeneity field",
+        position=0,
         xor=["use_ants"],
     )
     bias = File(argstr="-bias %s", desc="bias field")
@@ -224,14 +226,20 @@ class DWIBiasCorrect(MRTrix3Base):
     >>> bias_correct.inputs.in_file = 'dwi.mif'
     >>> bias_correct.inputs.use_ants = True
     >>> bias_correct.cmdline
-    'dwibiascorrect -ants dwi.mif dwi_biascorr.mif'
+    'dwibiascorrect ants dwi.mif dwi_biascorr.mif'
     >>> bias_correct.run()                             # doctest: +SKIP
     """
 
     _cmd = "dwibiascorrect"
     input_spec = DWIBiasCorrectInputSpec
     output_spec = DWIBiasCorrectOutputSpec
-
+    def _format_arg(self, name, trait_spec, value):
+        if name in ("use_ants", "use_fsl"):
+            ver = self.version
+            # Changed in version 3.0, after release candidates
+            if ver is not None and (ver[0] < "3" or ver.startswith("3.0_RC")):
+                return f"-{trait_spec.argstr}"
+        return super()._format_arg(name, trait_spec, value)
 
 class ResponseSDInputSpec(MRTrix3BaseInputSpec):
     algorithm = traits.Enum(

--- Proposal A
## Root cause
The `DWIBiasCorrect` interface builds the command line for **dwibiascorrect** using an `Enum` trait for the algorithm (`ants` or `fsl`).  
The trait was defined with `argstr='-%s'`, which prefixes the algorithm name with a dash (`-ants`, `-fsl`).  
`dwibiascorrect` expects the algorithm as a **positional argument** (e.g., `dwibiascorrect ants …`), not as an option.  
Consequently the generated command is invalid and MRtrix3 aborts with:

```
Error: argument algorithm: invalid choice: <in_file> (choose from 'ants', 'fsl')
```

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/nipype/interfaces/mrtrix3/preprocess.py
+++ b/nipype/interfaces/mrtrix3/preprocess.py
@@
 class DWIBiasCorrect(CommandLine):
@@
-    algorithm = traits.Enum(
-        "ants",
-        "fsl",
-        usedefault=True,
-        argstr="-%s",
-        desc="Bias field correction algorithm to use (ants or fsl).",
-    )
+    # NOTE: ``dwibiascorrect`` expects the algorithm name as a **positional**
+    # argument (e.g. ``dwibiascorrect ants …``).  Previously the trait used
+    # ``argstr="-%s"``, which incorrectly added a leading dash (``-ants``) and
+    # caused the command to fail.  The argument string is now defined without
+    # the dash so that the algorithm is placed correctly on the command line.
+    algorithm = traits.Enum(
+        "ants",
+        "fsl",
+        usedefault=True,
+        argstr="%s",
+        desc="Bias field correction algorithm to use (ants or fsl).",
+    )
@@
-    use_ants = traits.Bool(
-        False,
-        usedefault=True,
-        desc="Convenient flag to set algorithm='ants'.",
-    )
-
-    use_fsl = traits.Bool(
-        False,
-        usedefault=True,
-        desc="Convenient flag to set algorithm='fsl'.",
-    )
+    # Convenience booleans that set the ``algorithm`` trait.  They are kept for
+    # backward compatibility with older pipelines.  When either flag is set we
+    # override ``algorithm`` accordingly.
+    use_ants = traits.Bool(
+        False,
+        usedefault=True,
+        desc="Convenient flag to set algorithm='ants'.",
+    )
+
+    use_fsl = traits.Bool(
+        False,
+        usedefault=True,
+        desc="Convenient flag to set algorithm='fsl'.",
+    )
@@
-    def _parse_inputs(self, skip=None):
-        if self.inputs.use_ants:
-            self.inputs.algorithm = "ants"
-        if self.inputs.use_fsl:
-            self.inputs.algorithm = "fsl"
-        return super(DWIBiasCorrect, self)._parse_inputs(skip=skip)
+    def _parse_inputs(self, skip=None):
+        """
+        Resolve the convenience flags ``use_ants`` and ``use_fsl`` into the
+        ``algorithm`` Enum before the command line is assembled.
+        """
+        if self.inputs.use_ants:
+            self.inputs.algorithm = "ants"
+        if self.inputs.use_fsl:
+            self.inputs.algorithm = "fsl"
+        return super(DWIBiasCorrect, self)._parse_inputs(skip=skip)
```

*If the `DWIBiasCorrect` class lives in a different file (e.g. `dwibiascorrect.py`), apply the same changes there.*

## How to verify
1. **Re‑install the edited version** (or run the tests against the source tree).  
2. Execute the example from the issue:

```python
import nipype.interfaces.mrtrix3 as mrt
bc = mrt.DWIBiasCorrect()
bc.inputs.in_file = 'in.mif'
bc.inputs.out_file = 'out.mif'
bc.inputs.use_ants = True
res = bc.run()
```

3. Inspect the generated command (available via `bc.cmdline` or the log).  
  
--- Proposal B
## Root cause
`DWIBiasCorrect` builds the command line using an **algorithm** trait that is defined with a leading dash (`argstr='-%s'`) and a negative position.  
When the interface is executed the generated command looks like  

```
dwibiascorrect -mask <mask> -ants <in> <out>
```  

but the MRtrix3 tool expects the algorithm to be a **positional argument without a dash**, e.g.

```
dwibiascorrect ants -mask <mask> <in> <out>
```  

Because the algorithm argument is treated as an option, MRtrix3 interprets the next positional argument (`<in>`) as the algorithm value and raises the “invalid choice” error.

## Fix
```diff
--- a/nipype/interfaces/mrtrix3/preprocess.py
+++ b/nipype/interfaces/mrtrix3/preprocess.py
@@
 class DWIBiasCorrectInputSpec(BaseInterfaceInputSpec):
-    algorithm = traits.Enum('ants', 'fsl',
-                            argstr='-%s',
-                            position=-2,
-                            desc='Bias correction algorithm')
-
-    mask = File(exists=True,
-                argstr='-mask %s',
-                position=-1,
-                desc='Mask image')
-
-    in_file = File(exists=True,
-                   mandatory=True,
-                   argstr='%s',
-                   position=-3,
-                   desc='Input DWI')
-
-    out_file = File('dwibiascorrected.mif',
-                    argstr='%s',
-                    position=-4,
-                    desc='Output DWI')
-
-    use_ants = traits.Bool(False,
-                           usedefault=True,
-                           desc='Use ANTS for bias correction')
+    # ----------------------------------------------------------------------
+    # Positional arguments
+    # ----------------------------------------------------------------------
+    # The algorithm (ants or fsl) is a **positional** argument that follows the
+    # command name.  It must **not** be prefixed with a dash.  Setting
+    # ``argstr='%s'`` and ``position=0`` makes the generated command start with
+    # ``dwibiascorrect ants`` (or ``dwibiascorrect fsl``).
+    algorithm = traits.Enum('ants', 'fsl',
+                            argstr='%s',
+                            position=0,
+                            desc='Bias correction algorithm (ants or fsl)')
+
+    # Optional mask flag – placed after the algorithm but before the input
+    # and output files.
+    mask = File(exists=True,
+                argstr='-mask %s',
+                position=1,
+                desc='Mask image')
+
+    # Input and output files – the last two positional arguments.
+    in_file = File(exists=True,
+                   mandatory=True,
+                   argstr='%s',
+                   position=2,
+                   desc='Input DWI')
+
+    out_file = File('dwibiascorrected.mif',
+                    argstr='%s',
+                    position=3,
+                    desc='Output DWI')
+
+    # ----------------------------------------------------------------------
+    # Convenience booleans (kept for backward compatibility)
+    # ----------------------------------------------------------------------
+    use_ants = traits.Bool(False,
+                           usedefault=True,
+                           desc='Use ANTS for bias correction')
+    use_fsl = traits.Bool(False,
+                          usedefault=False,
+                          desc='Use FSL for bias correction')
@@
 class DWIBiasCorrect(BaseInterface):
@@
-    def _run_interface(self, runtime):
-        if se