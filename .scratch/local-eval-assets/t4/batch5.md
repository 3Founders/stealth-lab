

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
The *Notify on Virtual Levels* option only fires when a virtual level **changes**.  
When a skill reaches the maximum 200 000 000 XP the virtual level is already at its
cap (127) and therefore does not change any more, so no notification is sent.
The plugin never checks for the *exact* transition to the maximum XP value,
so users who have the virtual‑level notifier enabled never receive a “maxed‑out”
message.

## Fix
Add explicit handling for the transition to the maximum XP (200 M) inside the
`LevelNotifier`.  
When the XP of a skill crosses the `MAX_XP` threshold we fire the same level‑up
notification that is used for virtual‑level changes, but only once per skill.

```diff
diff --git a/src/main/java/dinkplugin/notifiers/LevelNotifier.java b/src/main/java/dinkplugin/notifiers/LevelNotifier.java
index 7c9a1f2..e3b4d8a 100644
--- a/src/main/java/dinkplugin/notifiers/LevelNotifier.java
+++ b/src/main/java/dinkplugin/notifiers/LevelNotifier.java
@@
 import java.util.HashMap;
 import java.util.Map;
 import javax.inject.Inject;
 import javax.inject.Singleton;
+import net.runelite.api.Skill;
+import net.runelite.api.events.StatChanged;
+import net.runelite.api.events.GameStateChanged;
+import net.runelite.api.GameState;
 
 @Singleton
 public class LevelNotifier extends BaseNotifier
 {
@@
     private final DinkPluginConfig config;
 
     /** Last virtual level we notified for each skill */
-    private final Map<Skill, Integer> lastVirtualLevel = new HashMap<>();
+    private final Map<Skill, Integer> lastVirtualLevel = new HashMap<>();
+
+    /** Whether we have already sent the “max‑xp” notification for a skill */
+    private final Map<Skill, Boolean> maxXpNotified = new HashMap<>();
 
     @Inject
     public LevelNotifier(DinkPluginConfig config, NotificationSender sender)
@@
-    @Subscribe
-    public void onStatChanged(StatChanged event)
-    {
-        Skill skill = event.getSkill();
-        int xp = event.getXp();
-
-        // ignore non‑skill stats (e.g. health, prayer)
-        if (skill == Skill.OVERALL) return;
-
-        // virtual‑level handling
-        if (config.notifyVirtualLevels())
-        {
-            int virtualLevel = calculateVirtualLevel(xp);
-            int previous = lastVirtualLevel.getOrDefault(skill, -1);
-
-            if (virtualLevel > previous)
-            {
-                // fire a level‑up notification (type LEVEL)
-                LevelNotificationData data = new LevelNotificationData(skill, virtualLevel, false);
-                sendNotification(NotificationType.LEVEL, data);
-                lastVirtualLevel.put(skill, virtualLevel);
-            }
-        }
-
-        // store the raw XP for possible future use
-        lastXp.put(skill, xp);
-    }
+    @Subscribe
+    public void onStatChanged(StatChanged event)
+    {
+        Skill skill = event.getSkill();
+        int xp = event.getXp();
+
+        // ignore non‑skill stats (e.g. health, prayer)
+        if (skill == Skill.OVERALL) return;
+
+        // -----------------------------------------------------------------
+        // 1️⃣  Virtual‑level notifications (existing behaviour)
+        // -----------------------------------------------------------------
+        if (config.notifyVirtualLevels())
+        {
+            int virtualLevel = calculateVirtualLevel(xp);
+            int previousVirtual = lastVirtualLevel.getOrDefault(skill, -1);
+
+            if (virtualLevel > previousVirtual)
+            {
+         
--- Proposal B
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
The language‑server’s completion logic only consulted the **public** type information (`type_info`) when it tried to provide field‑label completions for record access (`person.`).  
`type_info` is built from the module’s exported types, so for a record whose type is **private** (not `pub`) the server could not find any field information and therefore returned no completions.  

## Fix
* Extend the completion routine to also look up the record definition in the module’s **internal** type definitions (`module.type_definitions`).  
* When the type is a record (single‑constructor with labelled fields) we now return those labels regardless of the type’s visibility.  
* Keep the existing public‑type path for compatibility.

```diff
diff --git a/language-server/src/completion.rs b/language-server/src/completion.rs
index 7c9f2a1..e3b4d9c 100644
--- a/language-server/src/completion.rs
+++ b/language-server/src/completion.rs
@@
 use gleam_core::type_::{self, Type, TypeInfo};
 use gleam_core::type_::type_info::PublicTypeInfo;
 use lsp_types::{CompletionItem, CompletionItemKind, CompletionResponse, InsertTextFormat};
+use std::collections::HashMap;
 
 /// Context passed around while computing completions.
 pub struct CompletionContext<'a> {
     /// The typed module we are analysing.
-    pub module: &'a TypedModule,
+    pub module: &'a TypedModule,
     /// The current cursor position.
     pub position: Position,
     /// Cached map of public type information (type name → info).
-    pub public_type_info: &'a HashMap<EcoString, PublicTypeInfo>,
+    pub public_type_info: &'a HashMap<EcoString, PublicTypeInfo>,
 }
 
@@
 fn complete_record_field_access(
     ctx: &CompletionContext,
     expr: &TypedExpr,
 ) -> Option<Vec<CompletionItem>> {
-    // The original implementation only consulted `public_type_info`,
-    // which contains entries for *exported* types only.  Private record
-    // types therefore never yielded any field completions.
-    let typ = ctx.module.type_of(expr);
-    let name = match typ {
-        Type::App { name, .. } => name,
-        _ => return None,
-    };
-
-    // Look up the type in the public map – this works for public types.
-    if let Some(info) = ctx.public_type_info.get(name) {
-        if let TypeInfo::Record { fields, .. } = &info.typ {
-            return Some(
-                fields
-                    .iter()
-                    .map(|(label, _typ)| CompletionItem {
-                        label: label.clone(),
-                        kind: Some(CompletionItemKind::FIELD),
-                        insert_text_format: Some(InsertTextFormat::PlainText),
-                        ..Default::default()
-                    })
-                    .collect(),
-            );
-        }
-    }
-
-    // No public info – we previously gave up, which meant private records
-    // got no completions at all.
-    None
+    // -----------------------------------------------------------------
+    // 1️⃣  Determine the type of the expression we are accessing.
+    // -----------------------------------------------------------------
+    let typ = ctx.module.type_of(expr);
+    let type_name = match typ {
+        // Record types are always represented as an applied type with the
+        // constructor name (e.g. `Person`).
+        Type::App { name, .. } => name,
+        _ => return None,
+    };
+
+    // -----------------------------------------------------------------
+    // 2️⃣  Try the publi

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
--- Proposal B
## Root cause
`YeoJohnsonTransformer._inverse_transform_series` returned the *boolean mask* (`pos`/`neg`) instead of the reconstructed numeric values.  
When `inverse_transform` tried to assign this boolean Series back to the original DataFrame column, pandas interpreted the assignment as a boolean‑indexer operation, raising  

```
InvalidIndexError / IndexingError: Unalignable boolean Series provided as indexer
```  

The method also did not guarantee that the returned object had the same index as the input Series, which is required for a safe column assignment.

## Fix
```diff
diff --git a/feature_engine/transformation/yeojohnson.py b/feature_engine/transformation/yeojohnson.py
index 7c9a1f2..e3b4d8a 100644
--- a/feature_engine/transformation/yeojohnson.py
+++ b/feature_engine/transformation/yeojohnson.py
@@
-    def _inverse_transform_series(self, X, lmbda):
-        \"\"\"Inverse transform a single series using the Yeo‑Johnson formula.\n-
-
-        Parameters
-        ----------
-        X : pandas.Series
-            Series to be inverse transformed.\n-
-        lmbda : float
-            Lambda value estimated during fitting.\n-
-
-        Returns
-        -------
-        pandas.Series
-            Inverse transformed series.\n-
-        \"\"\n-
-        # mask for positive values (including zero)\n-        pos = X >= 0\n-        # mask for negative values\n-        neg = X < 0\n-\n-        # initialise output array\n-        x_inv = np.empty_like(X, dtype=\"float64\")\n-\n-        # when x >= 0\n-        if lmbda == 0:\n-            x_inv[pos] = np.exp(X[pos]) - 1\n-        else:  # lmbda != 0\n-            x_inv[pos] = np.power(X[pos] * lmbda + 1, 1 / lmbda) - 1\n-\n-        # when x < 0\n-        if lmbda != 2:\n-            x_inv[neg] = -(\n-                np.power(-X[neg] * (2 - lmbda) + 1, 1 / (2 - lmbda)) - 1\n-            )\n-        else:\n-            x_inv[neg] = -np.log(-X[neg] + 1)\n-\n-        # NOTE: the original implementation mistakenly returned the boolean\n-        # mask ``pos`` instead of the reconstructed values, which caused pandas\n-        # to treat the mask as a boolean indexer when assigning back to the\n-        # DataFrame column.  This resulted in the ``InvalidIndexError`` shown\n-        # in the issue.\n-        # The correct behaviour is to return a Series with the same index as\n-        # the input.\n-        return pd.Series(x_inv, index=X.index)\n+    def _inverse_transform_series(self, X, lmbda):
+        \"\"\"Inverse‑transform a single column using the Yeo‑Johnson formula.\n+\n+        Parameters\n+        ----------\n+        X : pandas.Series\n+            Series that has been transformed with :meth:`transform`.\n+        lmbda : float\n+            Lambda value estimated during ``fit``.\n+\n+        Returns\n+        -------\n+        pandas.Series\n+            The original (inverse‑transformed) values, **with the same index**\n+            as ``X``.\n+        \"\"\"\n+\n+        # Ensure we are working with a float array – the transformation may\n+        # have produced ``float64`` values, but explicit conversion guards\n+        # against integer dtypes.\n+        X_arr = X.astype(float).values\n+\n+        # Boolean masks for the two regions defined by the Yeo‑Johnson\n+        # transformation.\n+        pos = X_arr >= 0\n+        neg = ~pos\n+\n+        # Allocate the output array.\n+        x_inv = np.empty_like(X_arr, dtype=\"float64\")\n+\n+        # -----  x >= 0  ---------

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
`just` treats a missing recipe as a fatal error and always exits with a non‑zero status.  
There is no command‑line option to change this behaviour, so scripts that invoke `just` in many directories (e.g. via GNU parallel) cannot silently skip directories that do not define the requested recipe.

## Fix
Add a new flag `--if-present` (mirroring npm’s `--if-present`) that makes `just` exit with status 0 when the requested recipe cannot be found.  
The implementation consists of:

1. Extending the CLI definition (using **clap**) to recognise the new flag.
2. Propagating the flag into the runtime configuration (`Config`).
3. Adjusting the recipe‑lookup logic to honour the flag – if the recipe is missing and `if_present` is set, exit cleanly with status 0; otherwise keep the existing error handling.

```diff
diff --git a/src/cli.rs b/src/cli.rs
index 3a1f2c7..e8b9d4a 100644
--- a/src/cli.rs
+++ b/src/cli.rs
@@
 use clap::{App, Arg, ArgMatches};
 
 pub fn build_cli() -> App<'static, 'static> {
     App::new("just")
         .version(crate_version!())
         .about("🤖 Just a command runner")
@@
         .arg(
             Arg::with_name("quiet")
                 .short("q")
                 .long("quiet")
                 .help("Suppress recipe echoing")
                 .global(true),
         )
+        // New flag: exit with code 0 if the requested recipe does not exist
+        .arg(
+            Arg::with_name("if-present")
+                .long("if-present")
+                .help("Exit with status 0 when the requested recipe is not defined")
+                .global(true),
+        )
         .arg(
             Arg::with_name("recipe")
                 .help("The recipe to run")
                 .required(false)
                 .index(1),
         )
@@
 pub struct Config {
     pub quiet: bool,
     pub dry_run: bool,
     pub jobs: Option<usize>,
+    /// When true, `just` exits with status 0 if the requested recipe is missing.
+    pub if_present: bool,
     // … other fields …
 }
 
 impl Config {
     pub fn from_matches(matches: &ArgMatches) -> Self {
         Config {
             quiet: matches.is_present("quiet"),
             dry_run: matches.is_present("dry-run"),
             jobs: matches
                 .value_of("jobs")
                 .and_then(|s| s.parse::<usize>().ok()),
+            if_present: matches.is_present("if-present"),
             // … other fields …
         }
     }
 }
```

```diff
diff --git a/src/main.rs b/src/main.rs
index 7c9e1b2..d4f6a3c 100644
--- a/src/main.rs
+++ b/src/main.rs
@@
 fn main() -> Result<(), Box<dyn std::error::Error>> {
     let matches = cli::build_cli().get_matches();
     let config = cli::Config::from_matches(&matches);
 
-    // Load the Justfile (or the default one) and resolve the recipe to run.
-    let justfile = Justfile::load(&config)?;
-    let recipe_name = matches.value_of("recipe").unwrap_or("default");
-    let recipe = justfile
-        .recipe(recipe_name)
-        .ok_or_else(|| format!("recipe `{}` not found", recipe_name))?;
+    // Load the Justfile (or the default one) and resolve the recipe to run.
+    let justfile = Justfile::load(&config)?;
+    let recipe_name = matches.value_of("recipe").unwrap_or("default");
+
+    // Try to find the requested recipe.
+    let recipe_opt = justfile.recipe(recipe_name);
+
+    // If the recipe does not exist, honour the `--if-present` flag.
+    let recipe = match recipe_opt {
+        Some(r) => r,
+   
--- Proposal B
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
## Root cause
When a multicolumn block (`columns: 2`) ends exactly at the bottom of a page, the column layout algorithm thinks that the content *fits* into a single column.  
For `column‑fill: auto` (the default) the algorithm then reduces the effective column count to the number of columns that actually received content.  
If the block is followed by `page-break-before: always`, the next block starts on a new page, but the reduced column count is kept in the layout state, so the next multicolumn block is rendered with only one (narrow) column.  

In short: **the column layout incorrectly collapses empty columns when the previous multicolumn block exactly fills the page**, and the collapsed column count is not reset for the next block.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
diff --git a/weasyprint/layout/column.py b/weasyprint/layout/column.py
index 7c9e1a2..d4f3b8c 100644
--- a/weasyprint/layout/column.py
+++ b/weasyprint/layout/column.py
@@
 def columns_layout(context, box, bottom_space, skip_stack, containing_block,
                    column_gap, column_width, column_count, column_fill,
                    column_rule, column_rule_width, column_rule_style,
                    column_rule_color):
@@
-    # The number of columns may be reduced when column_fill is 'auto' and
-    # the content fits in fewer columns.  This is a CSS spec‑compliant
-    # optimisation, but it breaks when a column block ends exactly at a
-    # page break and the next block starts on a new page (the column count
-    # is not reset).  Keep the original column count for 'auto' unless we
-    # are balancing columns.
-    if column_fill == 'auto' and count < column_count:
-        # Do not reduce the column count – we still need the empty column
-        # to be rendered so that the next block starts with the correct
-        # number of columns.
-        count = column_count
-
-    # When column_fill is 'balance' we already compute the balanced height
-    # above; the column count stays as requested.
+    # ------------------------------------------------------------------
+    # NOTE ABOUT COLUMN COUNT
+    # ------------------------------------------------------------------
+    # For `column-fill: auto` the original implementation reduced the
+    # effective column count to the number of columns that actually received
+    # content.  This works for most cases but fails when a multicolumn block
+    # ends *exactly* at the bottom of a page and the next block starts on a
+    # new page (e.g. because of `page-break-before: always`).  The reduced
+    # column count is kept in the layout state and the following block is
+    # rendered with only one column, producing the visual bug reported in
+    # issue #XXX.
+    #
+    # The fix is to **never shrink the column count for `column-fill: auto`**.
+    # Empty columns must still be created (they will simply have height 0),
+    # ensuring that the layout state is reset correctly for the next page.
+    # ------------------------------------------------------------------
+
+    if column_fill != 'balance' and count < column_count:
+        # Preserve the original column count; empty columns are kept.
+        count = column_count
+
+    # When column_fill is 'balance' we already compute the balanced height
+    # above; the column count stays as requested.
*** End of File ***
```

### Explanation of the changes
* Added a detailed comment describing the bug and why the p
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
`Toml.decode` forwards map deserialization to `TomlMapDecoder` when the target descriptor’s kind is `StructureKind.MAP`.  
The existing implementation of `TomlMapDecoder` only handled maps whose **value type is a primitive `String`** – it read the raw TOML value as a string and placed it into the resulting `Map`.  
When the value type is a user‑defined serializable class (e.g. `Coin`), the decoder tries to treat the whole table entry as a primitive and ends up throwing a `MissingRequiredPropertyException` because the expected properties of `Coin` are never read.

## Fix
Extend `TomlMapDecoder` so that it decodes **any** value type, not only `String`.  
For each entry in the TOML table we now:
1. Resolve the value descriptor (`descriptor.getElementDescriptor(1)`).
2. Use the regular `TomlMainDecoder` to deserialize the corresponding `TomlNode` (which may be a table, array, primitive, etc.) with that descriptor.
3. Insert the resulting value into the resulting map.

```diff
diff --git a/ktoml-core/src/commonMain/kotlin/com/akuleshov7/ktoml/decoders/TomlMapDecoder.kt b/ktoml-core/src/commonMain/kotlin/com/akuleshov7/ktoml/decoders/TomlMapDecoder.kt
index e3b1c2a..f7a9d4b 100644
--- a/ktoml-core/src/commonMain/kotlin/com/akuleshov7/ktoml/decoders/TomlMapDecoder.kt
+++ b/ktoml-core/src/commonMain/kotlin/com/akuleshov7/ktoml/decoders/TomlMapDecoder.kt
@@
 package com.akuleshov7.ktoml.decoders
 
 import com.akuleshov7.ktoml.exceptions.MissingRequiredPropertyException
 import com.akuleshov7.ktoml.parsers.TomlParser
 import com.akuleshov7.ktoml.tree.nodes.TomlNode
+import com.akuleshov7.ktoml.tree.nodes.pairs.TomlKeyValue
+import com.akuleshov7.ktoml.tree.nodes.pairs.TomlTable
 import kotlinx.serialization.DeserializationStrategy
 import kotlinx.serialization.ExperimentalSerializationApi
 import kotlinx.serialization.descriptors.SerialDescriptor
 import kotlinx.serialization.descriptors.StructureKind
 import kotlinx.serialization.modules.EmptySerializersModule
 import kotlinx.serialization.modules.SerializersModule
 
 /**
  * Decoder for maps. It works only for maps with String keys.
@@
-    fun <T> decode(
-        deserializer: DeserializationStrategy<T>,
-        tomlFile: TomlFile,
-        inputConfig: TomlInputConfig = TomlInputConfig()
-    ): T {
-        // The original implementation only supported Map<String, String>.
-        // It read the raw TOML string values and put them into the map.
-        // This is insufficient for maps whose values are complex objects.
-        // The new implementation delegates the decoding of each value to the
-        // regular TomlMainDecoder, which already knows how to handle any
-        // serializable type.
-
-        // Verify that the descriptor really represents a map.
-        require(deserializer.descriptor.kind == StructureKind.MAP) {
-            "TomlMapDecoder can only be used with map descriptors"
-        }
-
-        // The root of a TOML file is always a table.
-        val rootTable = tomlFile.rootTable
-
-        // The map key type must be String – this is guaranteed by the library.
-        // The map value type is taken from the second element descriptor.
-        val valueDescriptor = deserializer.descriptor.getElementDescriptor(1)
-
-        // Build the resulting map.
-        val result = mutableMapOf<String, Any?>()
-
-        for ((key, node) in rootTable.entries) {
-            // Keys are always strings, so we can use them directly.
-            // For the
--- Proposal B
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
--- Proposal B
**## Root cause**  
The project’s Maven modules and the `Version` class still reference the previous release version (e.g., `0.3.x`). When a new development cycle starts the version strings must be bumped to the next snapshot (`0.4.0‑SNAPSHOT`). Because the old version is hard‑coded in many `pom.xml` files (as the parent version) and in `core/src/main/java/com/alibaba/fescar/core/protocol/Version.java`, the build and any tooling that relies on the version information report the wrong version.

**## Fix**  
Update every Maven module that inherits from the top‑level `fescar-all` parent to use the new parent version `0.4.0‑SNAPSHOT` and change the hard‑coded version constant in `Version.java` to `0.4.0`.

```diff
diff --git a/pom.xml b/pom.xml
index 1234567..89abcde 100644
--- a/pom.xml
+++ b/pom.xml
@@
   <groupId>com.alibaba.fescar</groupId>
-  <artifactId>fescar-all</artifactId>
-  <version>0.3.0</version>
+  <artifactId>fescar-all</artifactId>
+  <version>0.4.0-SNAPSHOT</version>
   <packaging>pom</packaging>
   <modules>
     <module>common</module>
@@

diff --git a/common/pom.xml b/common/pom.xml
index 39ec6b5..636da48 100644
--- a/common/pom.xml
+++ b/common/pom.xml
@@ -20,7 +20,7 @@
     <parent>
         <artifactId>fescar-all</artifactId>
         <groupId>com.alibaba.fescar</groupId>
-        <version>0.3.0</version>
+        <version>0.4.0-SNAPSHOT</version>
     </parent>
@@

diff --git a/config/pom.xml b/config/pom.xml
index ce1b75a..349d30d 100644
--- a/config/pom.xml
+++ b/config/pom.xml
@@ -20,7 +20,7 @@
     <parent>
         <artifactId>fescar-all</artifactId>
         <groupId>com.alibaba.fescar</groupId>
-        <version>0.3.0</version>
+        <version>0.4.0-SNAPSHOT</version>
     </parent>
@@

diff --git a/core/pom.xml b/core/pom.xml
index b95b057..faec231 100644
--- a/core/pom.xml
+++ b/core/pom.xml
@@ -21,7 +21,7 @@
     <parent>
         <artifactId>fescar-all</artifactId>
         <groupId>com.alibaba.fescar</groupId>
-        <version>0.3.0</version>
+        <version>0.4.0-SNAPSHOT</version>
     </parent>
@@

diff --git a/discovery/pom.xml b/discovery/pom.xml
index d031e0e..31a3ccd 100644
--- a/discovery/pom.xml
+++ b/discovery/pom.xml
@@ -21,7 +21,7 @@
     <parent>
         <artifactId>fescar-all</artifactId>
         <groupId>com.alibaba.fescar</groupId>
-        <version>0.3.0</version>
+        <version>0.4.0-SNAPSHOT</version>
     </parent>
@@

diff --git a/distribution/pom.xml b/distribution/pom.xml
index a130fd6..2ad49ef 100644
--- a/distribution/pom.xml
+++ b/distribution/pom.xml
@@ -20,7 +20,7 @@
     <parent>
         <artifactId>fescar-all</artifactId>
         <groupId>com.alibaba.fescar</groupId>
-        <version>0.3.0</version>
+        <version>0.4.0-SNAPSHOT</version>
     </parent>
@@

diff --git a/client/pom.xml b/client/pom.xml
index 5f6c8a1..d2e9b33 100644
--- a/client/pom.xml
+++ b/client/pom.xml
@@ -20,7 +20,7 @@
     <parent>
         <artifactId>fescar-all</artifactId>
         <groupId>com.alibaba.fescar</groupId>
-        <version>0.3.0</version>
+        <version>0.4.0-SNAPSHOT</version>
     </parent>
@@

diff --git a/server/pom.xml b/server/pom.xml
index 9c7e2b4..e3f1a9d 100644
--- a/server/pom.xml
+++ b/server/pom.xml
@@ -20,7 +20,7 @@
     <parent>
         <artifactId>fescar-all</artifactId>
         <groupId>com.alibaba.fescar</groupId>
-        <version>0.3.0</version>
+        <version>0.4.0-SNAPSHOT</version>
     </parent>
@@

diff --git a/core/src/main/java/com/aliba

=== TASK apigee__registry-1100 | repo apigee/registry | go
--- Issue
Registry tool: setting unknown `registry config` parameters seems to silently fail
`invalid` here could be any unrecognized parameter.

```
% registry config set invalid parameter
----
Need more help?
https://github.com/apigee/registry/wiki

% registry config set invalid 123      
----
Need more help?
https://github.com/apigee/registry/wiki
```
--- REAL FIX (ground truth)
diff --git a/cmd/registry/cmd/auth/print_token.go b/cmd/registry/cmd/auth/print_token.go
index 17e2260..d445d99 100644
--- a/cmd/registry/cmd/auth/print_token.go
+++ b/cmd/registry/cmd/auth/print_token.go
@@ -27,9 +27,6 @@ func printTokenCommand() *cobra.Command {
 		Short: "Invoke token-source to print a token",
 		Args:  cobra.ExactArgs(0),
 		RunE: func(cmd *cobra.Command, args []string) error {
-			cmd.SilenceUsage = true
-			cmd.SilenceErrors = true
-
 			_, c, err := config.ActiveRaw()
 			if err != nil {
 				if err == config.ErrNoActiveConfiguration {
diff --git a/cmd/registry/cmd/config/config.go b/cmd/registry/cmd/config/config.go
index 62f1d01..cc1c114 100644
--- a/cmd/registry/cmd/config/config.go
+++ b/cmd/registry/cmd/config/config.go
@@ -15,8 +15,6 @@
 package config
 
 import (
-	"fmt"
-
 	"github.com/apigee/registry/cmd/registry/cmd/config/configurations"
 	"github.com/spf13/cobra"
 )
@@ -38,11 +36,3 @@ func Command() *cobra.Command {
 	cmd.AddCommand(unsetCommand())
 	return cmd
 }
-
-type UnknownPropertyError struct {
-	property string
-}
-
-func (n UnknownPropertyError) Error() string {
-	return fmt.Sprintf("Unknown property: %q.", n.property)
-}
diff --git a/cmd/registry/cmd/config/configurations/activate.go b/cmd/registry/cmd/config/configurations/activate.go
index cf15bee..453829a 100644
--- a/cmd/registry/cmd/config/configurations/activate.go
+++ b/cmd/registry/cmd/config/configurations/activate.go
@@ -27,8 +27,6 @@ func activateCommand() *cobra.Command {
 		Short: "Activates an existing named configuration",
 		Args:  cobra.ExactArgs(1),
 		RunE: func(cmd *cobra.Command, args []string) error {
-			cmd.SilenceUsage = true
-			cmd.SilenceErrors = true
 			name := args[0]
 			if err := config.ValidateName(name); err != nil {
 				return err
diff --git a/cmd/registry/cmd/config/configurations/create.go b/cmd/registry/cmd/config/configurations/create.go
index 24d9c34..60dcfa9 100644
--- a/cmd/registry/cmd/config/configurations/create.go
+++ b/cmd/registry/cmd/config/configurations/create.go
@@ -30,8 +30,6 @@ func createCommand() *cobra.Command {
 		Example: "registry config configurations create localhost --registry.address='locahost:8080'",
 		Args:    cobra.ExactArgs(1),
 		RunE: func(cmd *cobra.Command, args []string) error {
-			cmd.SilenceUsage = true
-			cmd.SilenceErrors = true
 			name := args[0]
 			if err := config.ValidateName(name); err != nil {
 				return err
diff --git a/cmd/registry/cmd/config/configurations/delete.go b/cmd/registry/cmd/config/configurations/delete.go
index 8110121..b4ce254 100644
--- a/cmd/registry/cmd/config/configurations/delete.go
+++ b/cmd/registry/cmd/config/configurations/delete.go
@@ -28,8 +28,6 @@ func deleteCommand() *cobra.Command {
 		Short: "Deletes a named configuration",
 		Args:  cobra.MinimumNArgs(1),
 		RunE: func(cmd *cobra.Command, args []string) error {
-			cmd.SilenceUsage = true
-			cmd.SilenceErrors = true
 			for _, name := range args {
 				if err := config.ValidateNam
--- Proposal A
## Root cause
`registry config set` writes any key/value pair directly into the configuration file using Viper without checking whether the key is a supported configuration option. Because Viper accepts arbitrary keys, unknown parameters are silently stored (or ignored later) and the command exits with a success status, giving the impression that the operation succeeded even though the parameter has no effect.

## Fix
Add a whitelist of supported configuration keys and validate the key supplied to `registry config set`. If the key is not in the whitelist, return an error and exit with a non‑zero status so the user gets immediate feedback.

```diff
diff --git a/cmd/registry/cmd/config/set.go b/cmd/registry/cmd/config/set.go
index 3b2f1c7..e7a9d4b 100644
--- a/cmd/registry/cmd/config/set.go
+++ b/cmd/registry/cmd/config/set.go
@@
 package config
 
 import (
+	"fmt"
 	"os"
 
 	"github.com/spf13/cobra"
 	"github.com/spf13/viper"
 )
 
+// allowedConfigKeys is the whitelist of configuration parameters that the
+// Registry CLI recognises.  When a user attempts to set a key that is not in
+// this list we return an error instead of silently accepting the value.
+var allowedConfigKeys = map[string]struct{}{
+	// Core connection settings
+	"registry_endpoint": {},
+	// Project‑level defaults
+	"project":           {},
+	"location":          {},
+	// Output formatting
+	"output":            {},
+	"format":            {},
+	// Authentication
+	"auth_token":        {},
+	// Any future keys should be added here.
+}
+
+// isValidConfigKey reports whether the supplied key is a recognised configuration
+// parameter.
+func isValidConfigKey(key string) bool {
+	_, ok := allowedConfigKeys[key]
+	return ok
+}
+
 // setCmd represents the `registry config set` command.
 var setCmd = &cobra.Command{
 	Use:   "set <key> <value>",
 	Short: "Set a configuration value",
 	Args:  cobra.ExactArgs(2),
 	RunE: func(cmd *cobra.Command, args []string) error {
-		key := args[0]
-		value := args[1]
-
-		// Store the value in the configuration file.
-		viper.Set(key, value)
-		if err := viper.WriteConfig(); err != nil {
-			return err
-		}
-
-		fmt.Fprintf(os.Stdout, "Configuration %s set to %s\n", key, value)
-		return nil
+		key := args[0]
+		value := args[1]
+
+		// Validate the key before persisting it.
+		if !isValidConfigKey(key) {
+			return fmt.Errorf("unknown configuration parameter %q; use \"registry config list\" to see supported keys", key)
+		}
+
+		// Store the value in the configuration file.
+		viper.Set(key, value)
+		if err := viper.WriteConfig(); err != nil {
+			return err
+		}
+
+		fmt.Fprintf(os.Stdout, "Configuration %s set to %s\n", key, value)
+		return nil
 	},
 }
 
 func init() {
 	configCmd.AddCommand(setCmd)
 }
```

## How to verify
1. **Build the CLI** (or run `go test ./...` if a test suite exists) to ensure the code compiles.  
2. Execute a valid set operation, e.g.:

   ```bash
   $ registry config set project my-project
   Configuration project set to my-project
   ```

   The command should succeed and the value should be written to the config file.

3. Execute an invalid set operation:

   ```bash
   $ registry config set invalid 123
   ```

   Expected result:
   * Exit status is non‑zero.
   * Output contains an error similar to:
     ```
     unknown configuration parameter "invalid"; use "registry config list" to see supported keys
     ```

4. Verify that the invalid key was **not** added to the configuration file (e.g., inspect `
--- Proposal B
## Root cause
`registry config set` forwards the supplied key/value directly to Viper (`viper.Set(key, value)`).  
Viper will happily store any key, even if the application never reads it.  
Because the command does not validate the key against the list of supported configuration
options, an unknown key is silently ignored – the CLI prints the generic help banner but
does not return an error.  

## Fix
Add explicit validation of the configuration key before writing it.  
The set of supported keys is defined in `internal/config/keys.go` (new file) and the
`config set` command now checks the supplied key against this whitelist, returning a
clear error for unknown parameters.

```diff
--- a/cmd/config/set.go
+++ b/cmd/config/set.go
@@
 package config
 
 import (
+	"fmt"
 	"github.com/spf13/cobra"
 	"github.com/spf13/viper"
 )
 
 // setCmd represents the config set command
 var setCmd = &cobra.Command{
@@
-	RunE: func(cmd *cobra.Command, args []string) error {
-		key := args[0]
-		value := args[1]
-		viper.Set(key, value)
-		if err := viper.WriteConfig(); err != nil {
-			return err
-		}
-		return nil
-	},
+	RunE: func(cmd *cobra.Command, args []string) error {
+		key := args[0]
+		value := args[1]
+
+		// Validate the key against the list of known configuration options.
+		if !config.IsValidKey(key) {
+			return fmt.Errorf("unknown configuration key %q. Run \"registry config list\" to see supported keys", key)
+		}
+
+		viper.Set(key, value)
+		if err := viper.WriteConfig(); err != nil {
+			return err
+		}
+		return nil
+	},
 }
 
 func init() {
 	configCmd.AddCommand(setCmd)
 }
 
--- a/internal/config/keys.go
+++ b/internal/config/keys.go
@@
 package config
 
 import "strings"
 
 // Known configuration keys. Keep this list in sync with the documentation
 // and with the fields that the application actually reads.
 var knownConfigKeys = map[string]struct{}{
-	// TODO: add real keys when they exist
+	"project":          {}, // Google Cloud project ID
+	"location":         {}, // Default location for resources
+	"output":           {}, // Output format (json|yaml|text)
+	"api_endpoint":     {}, // Registry API endpoint
+	"auth_token":       {}, // Authentication token
+	"disable_tls":      {}, // Disable TLS verification (bool)
+	"cache_dir":        {}, // Directory used for local caching
+	"timeout_seconds":  {}, // HTTP timeout in seconds
 }
 
 // IsValidKey reports whether the supplied key is a known configuration option.
 func IsValidKey(key string) bool {
-	// TODO: Please implement
-	return false
+	_, ok := knownConfigKeys[strings.ToLower(key)]
+	return ok
 }
 
 // ListKeys returns a slice of all known configuration keys, sorted alphabetically.
 func ListKeys() []string {
-	// TODO: Please implement
-	return nil
+	keys := make([]string, 0, len(knownConfigKeys))
+	for k := range knownConfigKeys {
+		keys = append(keys, k)
+	}
+	// Simple alphabetical sort
+	sort.Strings(keys)
+	return keys
 }
```

### What was changed
1. **`cmd/config/set.go`**
   * Imported `fmt`.
   * Added validation step using a new helper `config.IsValidKey`.
   * Returns a descriptive error when the key is unknown.

2. **`internal/config/keys.go`** (new file)
   * Declares a whitelist `knownConfigKeys` containing all supported configuration options.
   * Implements `IsValidKey` to check membership (case‑insensitive).
   * Implements `ListKeys` to expose the list for potential future commands (e.g., `registry config list`).

## How to verify
1. **Build the CLI**  


=== TASK casey__just-1809 | repo casey/just | rust
--- Issue
completions and modules don't work together
The completions script uses `just --summary` to fetch a list of recipes. A recipe `bar` inside module `foo` is listed as `foo::bar`, however module recipes can't be invoked like this ("Justfile does not contain recipe `foo::bar`").

I'm not sure the best solution although I do personally find `foo::bar` easier syntax to read as `foo bar` in my mind looks like a recipe `foo` that takes a parameter `bar`. 

I wonder whether `foo::bar` should be added as additional valid syntax to run a module's recipe?
--- REAL FIX (ground truth)
diff --git a/README.md b/README.md
index f2cd1191..835dfbd8 100644
--- a/README.md
+++ b/README.md
@@ -2669,6 +2669,13 @@ $ just --unstable bar b
 B
 ```
 
+Or with path syntax:
+
+```sh
+$ just --unstable bar::b
+B
+```
+
 If a module is named `foo`, just will search for the module file in `foo.just`,
 `foo/mod.just`, `foo/justfile`, and `foo/.justfile`. In the latter two cases,
 the module file may have any capitalization.
diff --git a/src/justfile.rs b/src/justfile.rs
index 6105e154..8bec2956 100644
--- a/src/justfile.rs
+++ b/src/justfile.rs
@@ -2,7 +2,7 @@ use {super::*, serde::Serialize};
 
 #[derive(Debug)]
 struct Invocation<'src: 'run, 'run> {
-  arguments: &'run [&'run str],
+  arguments: Vec<&'run str>,
   recipe: &'run Recipe<'src>,
   settings: &'run Settings<'src>,
   scope: &'run Scope<'src, 'run>,
@@ -209,7 +209,7 @@ impl<'src> Justfile<'src> {
       _ => {}
     }
 
-    let argvec: Vec<&str> = if !arguments.is_empty() {
+    let mut remaining: Vec<&str> = if !arguments.is_empty() {
       arguments.iter().map(String::as_str).collect()
     } else if let Some(recipe) = &self.default {
       recipe.check_can_be_default_recipe()?;
@@ -220,15 +220,29 @@ impl<'src> Justfile<'src> {
       return Err(Error::NoDefaultRecipe);
     };
 
-    let arguments = argvec.as_slice();
-
     let mut missing = Vec::new();
     let mut invocations = Vec::new();
-    let mut remaining = arguments;
     let mut scopes = BTreeMap::new();
     let arena: Arena<Scope> = Arena::new();
 
-    while let Some((first, mut rest)) = remaining.split_first() {
+    while let Some(first) = remaining.first().copied() {
+      if first.contains("::") {
+        if first.starts_with(':') || first.ends_with(':') || first.contains(":::") {
+          missing.push(first.to_string());
+          remaining = remaining[1..].to_vec();
+          continue;
+        }
+
+        remaining = first
+          .split("::")
+          .chain(remaining[1..].iter().copied())
+          .collect();
+
+        continue;
+      }
+
+      let rest = &remaining[1..];
+
       if let Some((invocation, consumed)) = self.invocation(
         0,
         &mut Vec::new(),
@@ -241,12 +255,12 @@ impl<'src> Justfile<'src> {
         first,
         rest,
       )? {
-        rest = &rest[consumed..];
+        remaining = rest[consumed..].to_vec();
         invocations.push(invocation);
       } else {
-        missing.push((*first).to_owned());
+        missing.push(first.to_string());
+        remaining = rest.to_vec();
       }
-      remaining = rest;
     }
 
     if !missing.is_empty() {
@@ -273,7 +287,7 @@ impl<'src> Justfile<'src> {
       Self::run_recipe(
         &context,
         invocation.recipe,
-        invocation.arguments,
+        &invocation.arguments,
         &dotenv,
         search,
         &mut ran,
@@ -306,7 +320,7 @@ impl<'src> Justfile<'src> {
     search: &'run Search,
     parent: &'run Scope<'src, 'run>,
     first: &'run str,
-    rest: &'run [&'run str],
+
--- Proposal A
## Root cause
The completion script obtains the list of recipes with `just --summary`.  
For recipes defined inside a module the summary prints the *fully‑qualified* name
using the `::` separator (e.g. `foo::bar`).  

When a user tries to run such a recipe the CLI treats the whole string
`foo::bar` as a single recipe name.  The core parser only understands module
paths separated by **spaces** (`just foo bar`), so it reports:

```
Justfile does not contain recipe `foo::bar`
```

Thus completions that suggest `foo::bar` cannot be invoked.

## Fix
Treat `::` in the first command‑line argument as a module separator, expanding it
into separate arguments before the normal recipe‑lookup logic runs.

```diff
diff --git a/src/cli.rs b/src/cli.rs
index 7e3b9c2..d4f1a8e 100644
--- a/src/cli.rs
+++ b/src/cli.rs
@@
-    let mut args: Vec<String> = env::args_os()
-        .skip(1)
-        .map(|s| s.into_string().expect("invalid UTF-8 in arguments"))
-        .collect();
+    // -------------------------------------------------------------------------
+    // Compatibility shim for the completion script
+    // -------------------------------------------------------------------------
+    // The `just --summary` command prints module recipes using the `::`
+    // separator (e.g. `foo::bar`).  Historically the CLI only accepted the
+    // space‑separated form (`just foo bar`).  To make completions usable we
+    // expand the first argument on `::` and treat each component as a separate
+    // argument, exactly as if the user had typed spaces.
+    //
+    // Example:
+    //   $ just foo::bar baz
+    // becomes:
+    //   args = ["foo", "bar", "baz"]
+    //
+    // Normal invocations without `::` are left untouched.
+    let raw_args: Vec<String> = env::args_os()
+        .skip(1)
+        .map(|s| s.into_string().expect("invalid UTF-8 in arguments"))
+        .collect();
+
+    let mut args = Vec::new();
+    if let Some(first) = raw_args.get(0) {
+        // Split the recipe name on `::` and push each part.
+        for part in first.split("::") {
+            if !part.is_empty() {
+                args.push(part.to_string());
+            }
+        }
+        // Append the remaining arguments unchanged.
+        args.extend_from_slice(&raw_args[1..]);
+    }
 
-    // … later code uses `args` …
+    // … later code uses `args` …
```

* The change lives in `src/cli.rs`, the place where command‑line arguments are
  collected before the Justfile is parsed.
* No other parts of the code need to be altered because the rest of the
  implementation already understands a sequence of arguments that represent the
  module path followed by the recipe name.

## How to verify
1. Build the project: `cargo build --release`.
2. Run the summary command and note a module recipe, e.g.:

   ```sh
   $ just --summary | grep '^foo::bar'
   foo::bar   # description …
   ```

3. Execute the recipe using the `::` syntax:

   ```sh
   $ just foo::bar
   ```

   It should run exactly the same as `just foo bar` and **not** produce the
   “Justfile does not contain recipe `foo::bar`” error.

4. Verify that normal space‑separated invocations still work:

   ```sh
   $ just foo bar
   ```

5. Run the test suite (`cargo test`) to ensure no regressions.

If both forms work and the test suite passes, the issue is resolved.
--- Proposal B
## Root cause
The Bash completion script for **just** parses the output of `just --summary` to obtain the list of available recipes.  
When a recipe lives inside a module, `just --summary` prints it as `module::recipe`.  
However the actual command‑line syntax to invoke such a recipe is `just module recipe` (a space, not `::`).  
Because the completion script forwards the raw `module::recipe` strings to `compgen`, the generated completions are invalid – they suggest `foo::bar`, which `just` cannot execute.

## Fix (a unified diff with your best‑guess file paths and code)
```diff
--- a/completions/just.bash
+++ b/completions/just.bash
@@
-    # Get the list of recipes from `just --summary`.
-    local recipes=$(just --summary | awk '{print $1}')
+    # Get the list of recipes from `just --summary`.
+    #
+    # `just --summary` prints module recipes as `module::recipe`,
+    # but the correct invocation syntax is `just module recipe`
+    # (i.e. a space instead of `::`).  Convert the `::` separator to a
+    # space so that the completion suggestions are actually usable.
+    local recipes=$(just --summary \
+        | awk '{print $1}' \
+        | sed 's/::/ /g')
```

*Explanation of the change*  
- The original line simply extracted the first column of the summary output.  
- The patched version adds a `sed 's/::/ /g'` step that replaces every `::` with a single space, turning `foo::bar` into `foo bar`.  
- The comment clarifies why this transformation is required.

No other parts of the script need modification because the rest of the completion logic already works with a whitespace‑separated list of recipe names.

## How to verify
1. **Build/install the updated completion script**  
   ```bash
   # Assuming you have the repository checked out
   source completions/just.bash   # or copy it to /etc/bash_completion.d/
   ```

2. **Create a Justfile with a module recipe**  
   ```just
   module foo {
       bar:
           echo "inside foo::bar"
   }
   ```

3. **Check the summary output**  
   ```bash
   $ just --summary
   foo::bar
   ```

4. **Test Bash completion**  
   - Type `just f` and press `<TAB>` twice.  
   - The completion should suggest `foo` (the module) and, after a space, `bar`.  
   - The final suggestion should be `foo bar`, **not** `foo::bar`.

5. **Execute the recipe using the suggested completion**  
   ```bash
   $ just foo bar
   inside foo::bar
   ```

6. **Confirm that the old, invalid suggestion no longer appears**  
   - Run `compgen -W "$(just --summary | awk '{print $1}')" -- "foo::"` and verify it returns nothing.  
   - Run `compgen -W "$(just --summary | awk '{print $1}' | sed 's/::/ /g')" -- "foo "` and verify it returns `bar`.

If the completion now offers `foo bar` and the command executes successfully, the issue is resolved.

=== TASK kubernetes__kops-17270 | repo kubernetes/kops | go
--- Issue
Kubernetes 1.33 does not accept --cloud-config
/kind bug

kOps CI testing Kubernetes master is red since 2025-02-17, https://testgrid.k8s.io/kops-latest#kops-aws-amd64-conformance

`kube-apiserver --cloud-config` and `--cloud-provider` have been deprecated for quite some time, now they're removed for real and the newest API server won't come up.

**1. What `kops` version are you running? The command `kops version`, will display
 this information.**

v1.32.0-beta.1-20-g99ba10e2d5 (current [latest-ci-updown-green.txt](https://storage.googleapis.com/k8s-staging-kops/kops/releases/markers/master/latest-ci-updown-green.txt))

**2. What Kubernetes version are you running? `kubectl version` will print the
 version if a cluster is running or provide the Kubernetes version specified as
 a `kops` flag.**

Latest master,  https://storage.googleapis.com/k8s-release-dev/ci/v1.33.0-alpha.2.28+7fc8a86381d874

**3. What cloud provider are you using?**
AWS

**4.  What is the simplest way to reproduce this issue?**
See CI e.g. https://prow.k8s.io/view/gs/kubernetes-ci-logs/logs/e2e-kops-aws-amd64-conformance/1892186901553614848

**5. What happened after the commands executed?**
The API server fails with `Error: unknown flag: --cloud-config`

**6. What did you expect to happen?**
Green CI :-)


--- REAL FIX (ground truth)
diff --git a/nodeup/pkg/model/kube_apiserver.go b/nodeup/pkg/model/kube_apiserver.go
index f1636a44b4..c0bbd94415 100644
--- a/nodeup/pkg/model/kube_apiserver.go
+++ b/nodeup/pkg/model/kube_apiserver.go
@@ -573,7 +573,9 @@ func (b *KubeAPIServerBuilder) buildPod(ctx context.Context, kubeAPIServer *kops
 		return nil, fmt.Errorf("error building kube-apiserver flags: %v", err)
 	}
 
-	flags = append(flags, fmt.Sprintf("--cloud-config=%s", InTreeCloudConfigFilePath))
+	if b.IsKubernetesLT("1.33") {
+		flags = append(flags, fmt.Sprintf("--cloud-config=%s", InTreeCloudConfigFilePath))
+	}
 
 	pod := &v1.Pod{
 		TypeMeta: metav1.TypeMeta{
diff --git a/pkg/apis/kops/validation/legacy.go b/pkg/apis/kops/validation/legacy.go
index ecb8c71dd4..c07d8e63b2 100644
--- a/pkg/apis/kops/validation/legacy.go
+++ b/pkg/apis/kops/validation/legacy.go
@@ -21,6 +21,7 @@ import (
 	"net"
 	"strings"
 
+	"github.com/blang/semver/v4"
 	"k8s.io/apimachinery/pkg/util/validation/field"
 	"k8s.io/kops/pkg/apis/kops"
 	"k8s.io/kops/pkg/apis/kops/util"
@@ -40,12 +41,17 @@ func ValidateCluster(c *kops.Cluster, strict bool, vfsContext *vfs.VFSContext) f
 	// KubernetesVersion
 	// This is one case we return the error because a large part of the rest of the validation logic depends on a valid kubernetes version.
 
+	var k8sVersion *semver.Version
+	var err error
 	if c.Spec.KubernetesVersion == "" {
 		allErrs = append(allErrs, field.Required(fieldSpec.Child("kubernetesVersion"), ""))
 		return allErrs
-	} else if _, err := util.ParseKubernetesVersion(c.Spec.KubernetesVersion); err != nil {
-		allErrs = append(allErrs, field.Invalid(fieldSpec.Child("kubernetesVersion"), c.Spec.KubernetesVersion, "unable to determine kubernetes version"))
-		return allErrs
+	} else {
+		k8sVersion, err = util.ParseKubernetesVersion(c.Spec.KubernetesVersion)
+		if err != nil {
+			allErrs = append(allErrs, field.Invalid(fieldSpec.Child("kubernetesVersion"), c.Spec.KubernetesVersion, "unable to determine kubernetes version"))
+			return allErrs
+		}
 	}
 
 	if strict && c.Spec.Kubelet == nil {
@@ -72,7 +78,6 @@ func ValidateCluster(c *kops.Cluster, strict bool, vfsContext *vfs.VFSContext) f
 
 	var nonMasqueradeCIDR *net.IPNet
 	var serviceClusterIPRange *net.IPNet
-	var err error
 
 	if c.Spec.Networking.NonMasqueradeCIDR != "" {
 		_, nonMasqueradeCIDR, _ = net.ParseCIDR(c.Spec.Networking.NonMasqueradeCIDR)
@@ -182,8 +187,10 @@ func ValidateCluster(c *kops.Cluster, strict bool, vfsContext *vfs.VFSContext) f
 				}
 			}
 			if c.Spec.KubeAPIServer != nil && (strict || c.Spec.KubeAPIServer.CloudProvider != "") {
-				if c.Spec.KubeAPIServer.CloudProvider != "external" && k8sCloudProvider != c.Spec.KubeAPIServer.CloudProvider {
-					allErrs = append(allErrs, field.Forbidden(fieldSpec.Child("kubeAPIServer", "cloudProvider"), "Did not match cluster cloudProvider"))
+				if k8sVersion != nil && k8sVersion.LT(semver.MustParse("1.33.0")) {
+					if c.Spec.KubeAPIServer.CloudProvider != "external" && k8
--- Proposal A
## Root cause
`kops` always adds the flags `--cloud-provider=aws` and `--cloud-config=/etc/kubernetes/cloud-config` to the kube‑apiserver manifest.  
Since Kubernetes 1.33 the apiserver no longer supports these flags – they were removed after being deprecated for a long time.  
When a cluster is created with a Kubernetes version ≥ 1.33 the generated static‑pod manifest still contains the flags, causing the apiserver to exit with:

```
Error: unknown flag: --cloud-config
```

## Fix
* Stop emitting the `--cloud-config` (and the now‑redundant `--cloud-provider`) flags for Kubernetes versions ≥ 1.33.
* The decision is made while building the kube‑apiserver component, based on the cluster’s `Spec.KubernetesVersion`.
* Use the semver parser from `k8s.io/apimachinery/pkg/util/version` to compare versions.

```diff
--- a/pkg/model/components/kubeapiserver.go
+++ b/pkg/model/components/kubeapiserver.go
@@
 import (
     "fmt"
     "strings"
 
+    utilversion "k8s.io/apimachinery/pkg/util/version"
 
     api "k8s.io/kops/pkg/apis/kops"
     "k8s.io/kops/pkg/model"
 )
 
 // KubeAPIServerBuilder builds the kube-apiserver component.
@@
 func (b *KubeAPIServerBuilder) Build(c *api.Cluster) (*api.Component, error) {
@@
-    // Cloud provider flags (still required for older k8s versions)
-    args = append(args, "--cloud-provider=aws")
-    args = append(args, "--cloud-config=/etc/kubernetes/cloud-config")
+    // Cloud provider flags – only needed for Kubernetes < 1.33.
+    // In 1.33 the apiserver removed support for these flags.
+    // Parse the cluster’s Kubernetes version and add the flags only
+    // when the version is less than 1.33.0.
+    if c.Spec.KubernetesVersion != "" {
+        // Strip any leading "v" that may be present.
+        verStr := strings.TrimPrefix(c.Spec.KubernetesVersion, "v")
+        if ver, err := utilversion.ParseSemantic(verStr); err == nil {
+            // Compare against 1.33.0
+            if ver.LessThan(utilversion.MustParseSemantic("1.33.0")) {
+                args = append(args, "--cloud-provider=aws")
+                args = append(args, "--cloud-config=/etc/kubernetes/cloud-config")
+            }
+        } else {
+            // If we cannot parse the version, be safe and keep the flags
+            // (this mirrors the previous behaviour for unknown versions).
+            args = append(args, "--cloud-provider=aws")
+            args = append(args, "--cloud-config=/etc/kubernetes/cloud-config")
+        }
+    }
*** End of File
```

### What was changed
1. Imported `k8s.io/apimachinery/pkg/util/version` (aliased as `utilversion`).
2. Wrapped the addition of `--cloud-provider` and `--cloud-config` in a version check:
   * If the cluster’s Kubernetes version is **< 1.33.0**, the flags are kept (behaviour for older releases).
   * For **≥ 1.33.0**, the flags are omitted, preventing the “unknown flag” error.
3. Added a fallback to retain the flags when the version string cannot be parsed (conservative default).

## How to verify
1. **Unit test** – add a test in `pkg/model/components/kubeapiserver_test.go` that builds a component for a cluster with:
   * `KubernetesVersion: "1.32.0"` → manifest contains `--cloud-config`.
   * `KubernetesVersion: "1.33.0"` → manifest **does not** contain `--cloud-config`.
2. **Integration test** – spin up a kops cluster on AWS with `--kubernetes-version=1.33.0` (or newer) and verify that the kube‑apiserver pod starts successfully (no “unknown flag” error in its logs).
3. *
--- Proposal B
## Root cause
`kops` always adds the flags `--cloud-config` and `--cloud‑provider` to the kube‑apiserver command line.  
Starting with Kubernetes **v1.33** these flags were removed from the apiserver binary.  
When a cluster is created with a Kubernetes version ≥ 1.33 the generated manifest contains the now‑unknown flags, causing the apiserver to exit with:

```
Error: unknown flag: --cloud-config
```

The CI that builds a cluster with the latest master (which currently tracks v1.33) therefore fails.

## Fix
* Stop emitting `--cloud-config` and `--cloud-provider` for Kubernetes versions **≥ 1.33**.
* The decision is made when the component manifest is built (in `pkg/model/components/kubeapiserver.go`).
* Use the existing semver parser (`github.com/blang/semver`) that kops already depends on.

```diff
diff --git a/pkg/model/components/kubeapiserver.go b/pkg/model/components/kubeapiserver.go
index 7c9f2a1..e3b4d9c 100644
--- a/pkg/model/components/kubeapiserver.go
+++ b/pkg/model/components/kubeapiserver.go
@@
 import (
     "fmt"
 
     api "k8s.io/kops/pkg/apis/kops"
     "k8s.io/kops/pkg/apis/kops/v1alpha2"
+    "github.com/blang/semver"
 )
 
 // KubeAPIServer returns the apiserver component for the given cluster.
 func KubeAPIServer(cluster *v1alpha2.Cluster) (*api.Component, error) {
@@
-    // Cloud provider flags (still required for older Kubernetes versions)
-    if cluster.Spec.CloudProvider != "" {
-        args = append(args, "--cloud-provider="+cluster.Spec.CloudProvider)
-    }
-    if cluster.Spec.CloudConfig != "" {
-        args = append(args, "--cloud-config="+cluster.Spec.CloudConfig)
-    }
+    // Cloud provider flags – they were removed from the apiserver binary in v1.33.
+    // Emit them only for Kubernetes versions < 1.33.
+    emitCloudFlags := true
+    if cluster.Spec.KubernetesVersion != "" {
+        if v, err := semver.ParseTolerant(cluster.Spec.KubernetesVersion); err == nil {
+            // v.Major is always 1 for Kubernetes, we only care about the minor version.
+            if v.Major == 1 && v.Minor >= 33 {
+                emitCloudFlags = false
+            }
+        }
+    }
+    if emitCloudFlags {
+        if cluster.Spec.CloudProvider != "" {
+            args = append(args, "--cloud-provider="+cluster.Spec.CloudProvider)
+        }
+        if cluster.Spec.CloudConfig != "" {
+            args = append(args, "--cloud-config="+cluster.Spec.CloudConfig)
+        }
+    }
```

*The same logic can be applied to the controller‑manager component if desired, but the CI failure is caused by the apiserver, so fixing it here restores the green status.*

## How to verify
1. **Build kops** (or use the CI image) with the change applied.
2. Create a cluster that explicitly requests a Kubernetes version ≥ 1.33, e.g.:

   ```bash
   kops create cluster \
       --name=test.k8s.local \
       --state=s3://my-kops-state \
       --zones=us-east-1a \
       --kubernetes-version=v1.33.0 \
       --cloud=aws
   ```

3. After the cluster is created, inspect the generated kube‑apiserver manifest:

   ```bash
   kops get -o yaml --name=test.k8s.local
   # or directly on the node:
   cat /etc/kubernetes/manifests/kube-apiserver.yaml | grep cloud-
   ```

   The output **must not contain** `--cloud-config` or `--cloud-provider`.

4. Verify that the apiserver pod starts without the “unknown flag” error:

   ```bash
   kubectl get pods -n kube-system -l component=kube-apiserver
   kubectl logs -n kube-system kube-apiserver

=== TASK ajalt__mordant-77 | repo ajalt/mordant | kotlin
--- Issue
`Terminal.print()` ignores new line feeds at the end of Strings
I'm trying to interface Mordant with an existing logging structure by implementing the `Appendable` interface and forwarding the methods to Mordant using the `Terminal.print()` method, however I'm noticing that new line feeds at the end of `CharSequences/Strings` are getting ignored. Somehow Mordant is stripping them.

**Sample code:**
```
class TerminalAppendable(private val terminal: Terminal): Appendable {

    override fun append(charSequence: CharSequence): Appendable {
        terminal.print(charSequence)
        return this
    }

    override fun append(charSequence: CharSequence, start: Int, end: Int): Appendable {
        terminal.print(charSequence.subSequence(start, end))
        return this
    }

    override fun append(char: Char): Appendable {
        terminal.print(char.toString())
        return this
    }
}
```

```
val appendable = TerminalAppendable(Terminal())
appendable.appendLine("Foo Bar")
appendable.appendLine("Lorem Ipsum")
// Result:   `Foo BarLorem Ipsum`
// Expected: `Foo Bar\nLorem Ipsum\n`

appendable.append("Foo Bar\n")
appendable.append("Lorem Ipsum\n")
// Result:   `Foo BarLorem Ipsum`
// Expected: `Foo Bar\nLorem Ipsum\n`

appendable.append("Foo\nBar\n")
appendable.append("Lorem\nIpsum\n")
// Result:   `Foo\nBarLorem\nIpsum`
// Expected: `Foo\nBar\nLorem\nIpsum\n`
```
--- REAL FIX (ground truth)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 6f85a06b5..4639aabe1 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,5 +1,10 @@
 # Changelog
 
+## 2.0.0-beta8
+### Changed
+- Stop stripping trailing newline from text when using `Whitespace.PRE` [(#75)](https://github.com/ajalt/mordant/issues/75)
+
+
 ## 2.0.0-beta8
 ### Added
 - Implemented `hideInput` for prompts on native targets [(#63)](https://github.com/ajalt/mordant/issues/63)
diff --git a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/internal/Parsing.kt b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/internal/Parsing.kt
index fad79fb9b..1ccb4343f 100644
--- a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/internal/Parsing.kt
+++ b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/internal/Parsing.kt
@@ -78,7 +78,7 @@ private fun splitWords(chunk: Chunk): List<Chunk> {
 }
 
 /** Split a flat list of chunks into a list of lines, removing linebreak chunks */
-private fun splitLines(words: Iterable<Chunk>): List<Line> {
+private fun splitLines(words: List<Chunk>): List<Line> {
     val lines = mutableListOf<Line>()
     var line = mutableListOf<Span>()
 
@@ -92,6 +92,11 @@ private fun splitLines(words: Iterable<Chunk>): List<Line> {
     }
 
     if (line.isNotEmpty()) lines += Line(line)
+    words.lastOrNull()?.let {
+        if (it.text.endsWith("\n")) {
+            lines.add(Line(listOf(), endStyle = it.style))
+        }
+    }
 
     return lines
 }
diff --git a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/markdown/MarkdownRenderer.kt b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/markdown/MarkdownRenderer.kt
index 9665bca43..f62581fbd 100644
--- a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/markdown/MarkdownRenderer.kt
+++ b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/markdown/MarkdownRenderer.kt
@@ -287,9 +287,8 @@ internal class MarkdownRenderer(
             }
 
             MarkdownTokenTypes.EOL -> {
-                // Add an extra linebreak, since the first one will get folded away by foldLines.
                 // Parse the text rather than hard coding the return value to support NEL and LS.
-                Lines(listOf(EMPTY_LINE) + parseText(node.nodeText(), DEFAULT_STYLE).lines)
+                Lines(parseText(node.nodeText(), DEFAULT_STYLE).lines)
             }
 
             else -> error("Unexpected token when parsing inlines: $node; [${node.type}:'${node.nodeText().take(10)}'}]")

--- Proposal A
## Root cause
`Terminal.print(CharSequence)` strips a trailing line‑feed (`'\n'`) from the supplied text before writing it to the underlying output.  
The implementation was written to avoid printing a double newline when `println` was used, but it unintentionally removes **any** newline that appears at the end of the string.  
Because `Appendable.append…` forwards the raw `CharSequence` to `Terminal.print`, the final `'\n'` of each appended chunk is lost, which leads to the observed concatenation of lines.

## Fix (a unified diff with your best‑guess file paths and code)

```diff
--- a/src/main/kotlin/com/github/ajalt/mordant/Terminal.kt
+++ b/src/main/kotlin/com/github/ajalt/mordant/Terminal.kt
@@
-    /**
-     * Print the given text to the terminal. If the text ends with a newline it is
-     * stripped because `println` will add its own newline.
-     */
-    fun print(text: CharSequence) {
-        // NOTE: Historically we removed a trailing '\n' to avoid double newlines.
-        // This broke Appendable implementations that forward raw strings.
-        val str = text.toString()
-        if (str.endsWith('\n')) {
-            // Write everything except the final newline; the caller can add it
-            // explicitly with `println` if desired.
-            output.print(str.substring(0, str.length - 1))
-        } else {
-            output.print(str)
-        }
-        output.flush()
-    }
+    /**
+     * Print the given text to the terminal **exactly** as supplied.
+     *
+     * The previous implementation removed a trailing `'\n'` under the assumption
+     * that callers would use `println` for line‑terminated output.  This caused
+     * `Terminal.print` to silently drop the final newline, breaking any code
+     * (including `Appendable` adapters) that forwards raw strings containing
+     * line‑feeds.  The method now writes the full text unchanged; callers who
+     * want an automatic newline should use `println` instead.
+     */
+    fun print(text: CharSequence) {
+        // Write the text verbatim – do **not** strip trailing newlines.
+        output.print(text.toString())
+        output.flush()
+    }
```

### What changed
* Removed the conditional that trimmed a trailing `'\n'`.
* Updated the KDoc to explain the new behaviour and why the change was required.
* Kept the flush call unchanged.

## How to verify
1. **Add a unit test** (or run the snippet from the issue) that captures the terminal output using a `StringBuilder`‑backed `Terminal`:

```kotlin
@Test
fun `appendable forwards newlines correctly`() {
    val sb = StringBuilder()
    val terminal = Terminal(terminal = TerminalBuilder.builder()
        .streams(OutputStream.nullOutputStream(), OutputStream.nullOutputStream())
        .writer { sb.append(it) }
        .build())
    val appendable = TerminalAppendable(terminal)

    // Using the same calls as in the issue description
    appendable.appendLine("Foo Bar")
    appendable.appendLine("Lorem Ipsum")
    assertEquals("Foo Bar\nLorem Ipsum\n", sb.toString())

    sb.clear()
    appendable.append("Foo Bar\n")
    appendable.append("Lorem Ipsum\n")
    assertEquals("Foo Bar\nLorem Ipsum\n", sb.toString())

    sb.clear()
    appendable.append("Foo\nBar\n")
    appendable.append("Lorem\nIpsum\n")
    assertEquals("Foo\nBar\nLorem\nIpsum\n", sb.toString())
}
```

2. **Run the test suite** (`./gradlew test`). All existing tests should still pass, and the new test should now succeed, confirming that `
--- Proposal B
## Root cause
`Terminal.print()` splits the supplied text on `'\n'` using Kotlin’s default `String.split()` implementation.  
The default split uses `limit = 0`, which **drops trailing empty strings**.  
When the input ends with a newline, the split discards the final empty element, so the newline is never written to the terminal.  
Consequently any `Appendable` that forwards to `Terminal.print()` loses newline characters at the end of the supplied `CharSequence`.

## Fix
Update the `print` implementation to keep trailing empty strings when splitting.  
The easiest way is to call `split('\n', limit = -1)`, which tells Kotlin not to discard trailing empties.

```diff
diff --git a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/terminal/Terminal.kt b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/terminal/Terminal.kt
index 3a5fb1bca..d4e9c7f12 100644
--- a/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/terminal/Terminal.kt
+++ b/mordant/src/commonMain/kotlin/com/github/ajalt/mordant/terminal/Terminal.kt
@@
     /**
      * Print a string to the terminal without adding a newline.
      *
      * This method respects the terminal's width and wraps lines as needed.
      */
-    public fun print(text: Any?) {
-        val s = text?.toString() ?: return
-        // Split on newlines, but the default split drops trailing empty strings,
-        // which means a trailing newline is lost.
-        val lines = s.split('\n')
-        for ((i, line) in lines.withIndex()) {
-            write(line)
-            if (i != lines.lastIndex) newline()
-        }
-    }
+    public fun print(text: Any?) {
+        val s = text?.toString() ?: return
+        // Split on newlines **preserving** trailing empty strings.
+        // Using limit = -1 prevents Kotlin from discarding the final empty element,
+        // which ensures a trailing '\\n' is emitted.
+        val lines = s.split('\n', limit = -1)
+        for ((i, line) in lines.withIndex()) {
+            write(line)
+            // Emit a newline after every line **except** the very last one.
+            // If the original string ended with a newline, the last element of
+            // `lines` will be an empty string, and the loop will write that empty
+            // line followed by a newline, preserving the original behaviour.
+            if (i != lines.lastIndex) newline()
+        }
+    }
```

### What changed
* Replaced the plain `split('\n')` with `split('\n', limit = -1)` to keep trailing empty strings.
* Added explanatory comments.
* The rest of the logic (writing each line and inserting newlines between them) stays the same, but now a trailing newline is correctly emitted.

## How to verify
1. **Add a unit test** (or run the sample code from the issue) that uses `TerminalAppendable`:

```kotlin
@Test
fun `print preserves trailing newlines`() {
    val recorder = TerminalRecorder()          // a Terminal that records output
    val appendable = TerminalAppendable(Terminal(recorder))

    appendable.appendLine("Foo Bar")
    appendable.appendLine("Lorem Ipsum")
    assertEquals("Foo Bar\nLorem Ipsum\n", recorder.output)

    recorder.clear()
    appendable.append("Foo Bar\n")
    appendable.append("Lorem Ipsum\n")
    assertEquals("Foo Bar\nLorem Ipsum\n", recorder.output)

    recorder.clear()
    appendable.append("Foo\nBar\n")
    appendable.append("Lorem\nIpsum\n")
    assertEquals("Foo\nBar\nLorem\nIpsum\n", recorder.output)
}
```

2. **Run the test suite** (`

=== TASK jenkinsci__prometheus-plugin-598 | repo jenkinsci/prometheus-plugin | java
--- Issue
jenkins_builds_last_build_result_ordinal is missing in the  prometheus
### Jenkins and plugins versions report

<details>

  <summary>Environment</summary>

  <!-- Paste your environment details below -->
  ```
Jenkins: 2.414.2 
prometheus:2.3.2
metrics:4.2.18-442.v02e107157925
  ```

</details>


### What Operating System are you using (both controller, and any agents involved in the problem)?

OS: Linux

### Reproduction steps

We have observed changes in Prometheus version 2.3.2. The last build results ordinal gauge doesn't track the status of the running job. Previously, we were using Prometheus version 2.2.3 and didn't encounter such issues. Can someone help us with this?

### Expected Results

 The last build results ordinal gauge should track the status of the running job

```
# TYPE default_jenkins_builds_last_build_result_ordinal gauge
default_jenkins_builds_last_build_result_ordinal{jenkins_job="content/job-X.Y.Z-rhel8",repo="NA",buildable="true",} -1.0"
```


### Actual Results

we see the build job in default_jenkins_builds_last_build_result_ordinal gauge after it gets completed

```
# TYPE default_jenkins_builds_last_build_result_ordinal gauge
default_jenkins_builds_last_build_result_ordinal{jenkins_job="content/job-X.Y.Z-rhel8",repo="NA",buildable="true",} 1.0"
```

### Anything else?

_No response_

### Are you interested in contributing a fix?

_No response_
--- REAL FIX (ground truth)
diff --git a/README.md b/README.md
index 88a0d73..8c3c2ac 100644
--- a/README.md
+++ b/README.md
@@ -1,7 +1,7 @@
 # Jenkins Prometheus Metrics Plugin
 
 ## About
-Jenkins Prometheus Plugin expose an endpoint (default `/prometheus`) with metrics where a Prometheus Server can scrape.
+Jenkins Prometheus Plugin expose an endpoint (default `/prometheus/`) with metrics where a Prometheus Server can scrape.
 
 Documentation can be found [here](https://plugins.jenkins.io/prometheus)
 
@@ -13,6 +13,11 @@ Please note that the documentation is a WIP.
 - Metrics from [Metrics-plugin](https://github.com/jenkinsci/metrics-plugin)
 - Metrics from this plugin. Refer [Prometheus-plugin](docs/metrics/index.md)
 
+## Scraping the endpoint
+The endpoint you've configured or the default endpoint `/prometheus/` in case you didn't configure an endpoint, needs to 
+end with a trailing slash when you configure the endpoint in your scraping tool. If you miss adding the trailing slash
+you'll get a 302 response with a redirection to the endpoint ending with a slash. Some tools cannot handle this well.
+
 ## Configuring the plugin
 You can find some examples in this documentation [Configuring Plugin](docs/configuration/configuration.md)
 
@@ -20,7 +25,7 @@ You can find some examples in this documentation [Configuring Plugin](docs/confi
 
 `PROMETHEUS_NAMESPACE` Prefix of metric (Default: `default`).
 
-`PROMETHEUS_ENDPOINT` REST Endpoint (Default: `prometheus`)
+`PROMETHEUS_ENDPOINT` REST Endpoint (Default: `/prometheus/`)
 
 `COLLECTING_METRICS_PERIOD_IN_SECONDS` Async task period in seconds (Default: `120` seconds)
 
diff --git a/src/main/java/org/jenkinsci/plugins/prometheus/collectors/builds/BuildResultOrdinalGauge.java b/src/main/java/org/jenkinsci/plugins/prometheus/collectors/builds/BuildResultOrdinalGauge.java
index 84125e5..3558baf 100644
--- a/src/main/java/org/jenkinsci/plugins/prometheus/collectors/builds/BuildResultOrdinalGauge.java
+++ b/src/main/java/org/jenkinsci/plugins/prometheus/collectors/builds/BuildResultOrdinalGauge.java
@@ -37,17 +37,16 @@ public class BuildResultOrdinalGauge extends BuildsMetricCollector<Run<?, ?>, Ga
             return;
         }
 
+        int ordinal = -1;
         Result result = jenkinsObject.getResult();
-        if (result == null) {
-            return;
+        if (null != result) {
+            ordinal = result.ordinal;
         }
 
         if (labelValues == null) {
-            this.collector.labels().set(result.ordinal);
+            this.collector.labels().set(ordinal);
         } else {
-            this.collector.labels(labelValues).set(result.ordinal);
+            this.collector.labels(labelValues).set(ordinal);
         }
-
-
     }
 }

--- Proposal A
## Root cause
`jenkins_builds_last_build_result_ordinal` is only updated when a build finishes.  
The metric is populated from `Run#getResult()`, which returns **null** while the
build is still running. The existing implementation skips the metric when the
result is `null`, so the gauge disappears during the build and re‑appears only
after completion. The expected behaviour (as in earlier plugin versions) is
to expose the gauge with a sentinel value (‑1) while the build is in progress.

## Fix
Update the metric collection code to always emit the gauge. When the build’s
result is `null` (i.e., the build is still running) emit `-1` instead of
omitting the metric.

```diff
--- a/src/main/java/io/jenkins/plugins/prometheus/JobMetrics.java
+++ b/src/main/java/io/jenkins/plugins/prometheus/JobMetrics.java
@@
     private void collectBuildResultMetrics(Run<?, ?> build, List<MetricFamilySamples> mfs) {
-        Result result = build.getResult();
-        if (result == null) {
-            // build is still running – metric was previously omitted
-            return;
-        }
-        GaugeMetricFamily gauge = new GaugeMetricFamily(
-                "jenkins_builds_last_build_result_ordinal",
-                "Ordinal of the last build result (SUCCESS=0, UNSTABLE=1, FAILURE=2, NOT_BUILT=3, ABORTED=4). " +
-                "Value -1 indicates the build is still running.",
-                buildLabels(build));
-        gauge.addMetric(buildLabels(build), result.ordinal());
-        mfs.add(gauge);
+        // The ordinal of the build result.  When the build is still running
+        // `Run#getResult()` returns null – we expose a sentinel value of -1
+        // (this matches the behaviour of earlier plugin releases).
+        Result result = build.getResult();
+        int ordinal = (result == null) ? -1 : result.ordinal();
+
+        GaugeMetricFamily gauge = new GaugeMetricFamily(
+                "jenkins_builds_last_build_result_ordinal",
+                "Ordinal of the last build result (SUCCESS=0, UNSTABLE=1, FAILURE=2, NOT_BUILT=3, ABORTED=4). " +
+                "Value -1 indicates the build is still running.",
+                buildLabels(build));
+        gauge.addMetric(buildLabels(build), ordinal);
+        mfs.add(gauge);
     }
```

*Explanation of changes*  

* Removed the early‑return that suppressed the metric when `result == null`.  
* Introduced a sentinel `ordinal = -1` for running builds.  
* Updated the metric description to document the sentinel value.  
* The gauge is now always emitted, guaranteeing that the metric exists for a
  running job and shows `-1` until the build finishes.

## How to verify
1. Build the plugin with the changes and install it on a Jenkins instance
   (e.g., Jenkins 2.414.2).  
2. Start a job that takes a noticeable amount of time (e.g., a `sleep` step).  
3. While the job is **running**, query the Prometheus endpoint
   (`http://<jenkins>/prometheus`) and look for the metric
   `jenkins_builds_last_build_result_ordinal`. It should be present and have a
   value of `-1`:

   ```
   # TYPE jenkins_builds_last_build_result_ordinal gauge
   jenkins_builds_last_build_result_ordinal{jenkins_job="my-job",repo="NA",buildable="true"} -1
   ```

4. Let the job finish. Re‑query the endpoint; the metric should now show the
   appropriate ordinal (e.g., `0` for SUCCESS, `2` for FAILURE, etc.).  

5. Repeat with multiple concurrent builds to ensure each running build reports
   `-1` independently.

If the m
--- Proposal B
## Root cause
`jenkins_builds_last_build_result_ordinal` is produced by **`LastBuildResultOrdinalGauge`**.  
The collector only emitted a value when the last build had a non‑null `Result`.  
When a build is running `Run#getResult()` returns `null`, so the gauge was **not emitted** (or emitted with the previous completed value).  
With the newer Prometheus client library the missing sample is no longer kept, therefore the metric disappears while the job is executing.

## Fix
Emit a value for the gauge even when the last build is still running.  
When `run.isBuilding()` (or `run.getResult() == null`) we set the metric to **‑1** (the convention used by the plugin for “running”).  

```diff
diff --git a/src/main/java/org/jenkinsci/plugins/prometheus/metrics/jobs/LastBuildResultOrdinalGauge.java b/src/main/java/org/jenkinsci/plugins/prometheus/metrics/jobs/LastBuildResultOrdinalGauge.java
index 7c9a1f2..e3b4d8a 100644
--- a/src/main/java/org/j.../metrics/jobs/LastBuildResultOrdinalGauge.java
+++ b/src/main/java/org/j.../metrics/jobs/LastBuildResultOrdinalGauge.java
@@
 import io.prometheus.client.Gauge;
 import org.jenkinsci.plugins.prometheus.util.ArrayUtils;
 import org.jenkinsci.plugins.prometheus.util.Jobs;
 import org.jenkinsci.plugins.prometheus.util.Runs;
+import hudson.model.Result;
 
 /**
  * Gauge exposing the ordinal of the last build result.
  * <p>
  * The ordinal follows the order defined by {@link hudson.model.Result}.
  * When a build is still running the gauge reports **-1**.
  */
 public class LastBuildResultOrdinalGauge extends AbstractJobMetric {
@@
     public LastBuildResultOrdinalGauge(String prefix, String[] labelNames) {
         super(prefix, labelNames);
     }
 
     @Override
     public List<MetricFamilySamples> collect() {
-        List<MetricFamilySamples.Sample> samples = new ArrayList<>();
-        for (Job<?, ?> job : Jobs.getAllJobs()) {
-            Run<?, ?> last = Runs.getLastCompletedRun(job);
-            if (last == null) {
-                continue;
-            }
-            // last.getResult() is never null for a completed run
-            int ordinal = last.getResult().ordinal();
-            samples.add(new MetricFamilySamples.Sample(
-                    metricName,
-                    labelNames,
-                    labelValues(job),
-                    ordinal));
-        }
-        return familySamples(samples);
+        List<MetricFamilySamples.Sample> samples = new ArrayList<>();
+        for (Job<?, ?> job : Jobs.getAllJobs()) {
+            // We want the *last* build, even if it is still running.
+            Run<?, ?> last = Runs.getLastRun(job);
+            if (last == null) {
+                // No builds at all – skip the job.
+                continue;
+            }
+
+            int value;
+            // If the build is still executing, report -1.
+            if (last.isBuilding() || last.getResult() == null) {
+                value = -1;
+            } else {
+                // For a completed build the result is never null.
+                Result r = last.getResult();
+                value = (r != null) ? r.ordinal() : -1;
+            }
+
+            samples.add(new MetricFamilySamples.Sample(
+                    metricName,
+                    labelNames,
+                    labelValues(job),
+                    value));
+        }
+        return familySamples(samples);
     }
 }
```

### Explanation of changes
1. **Import `hudson.model.Result`** – needed to

=== TASK modin-project__modin-7395 | repo modin-project/modin | python
--- Issue
BUG: Series.duplicated doesn't preserve Series name
### Modin version checks

- [X] I have checked that this issue has not already been reported.

- [X] I have confirmed this bug exists on the latest released version of Modin.

- [ ] I have confirmed this bug exists on the main branch of Modin. (In order to do this you can follow [this guide](https://modin.readthedocs.io/en/stable/getting_started/installation.html#installing-from-the-github-main-branch).)


### Reproducible Example

```python
In [1]: import modin.pandas as pd

In [2]: pd.Series([1,2,3], name='a').duplicated()
Out[2]: 
0    False
1    False
2    False
dtype: bool

In [3]: pd.Series([1,2,3], name='a').duplicated().name
```


### Issue Description

The name is not preserved

I noticed this in [Narwhals](https://github.com/narwhals-dev/narwhals/pull/870)

### Expected Behavior

pandas preserves the name:
```
In [1]: import pandas as pd

In [2]: pd.Series([1,2,3], name='a').duplicated().name
Out[2]: 'a'
```

### Error Logs

<details>

```python-traceback

Replace this line with the error backtrace (if applicable).

```

</details>


### Installed Versions

<details>

INSTALLED VERSIONS
------------------
commit                : c8bbca8e4e00c681370e3736b2f73bb0352408c3
python                : 3.11.9.final.0
python-bits           : 64
OS                    : Linux
OS-release            : 5.15.153.1-microsoft-standard-WSL2
Version               : #1 SMP Fri Mar 29 23:14:13 UTC 2024
--- REAL FIX (ground truth)
diff --git a/modin/pandas/series.py b/modin/pandas/series.py
index d18a0bec..00083200 100644
--- a/modin/pandas/series.py
+++ b/modin/pandas/series.py
@@ -1022,7 +1022,12 @@ class Series(BasePandasDataset):
         """
         Indicate duplicate Series values.
         """
-        return self.to_frame().duplicated(keep=keep)
+        name = self.name
+        result = self.to_frame().duplicated(keep=keep)
+        # DataFrame.duplicated drops the name, so we need to manually restore it
+        if name is not None:
+            result.name = name
+        return result
 
     def eq(
         self, other, level=None, fill_value=None, axis=0

--- Proposal A
## Root cause
`Series.duplicated` builds a new `Series` from the result of the query‑compiler operation but does **not** copy the original series’ ``name`` attribute. Consequently the returned series has ``name=None`` while pandas correctly propagates the name.

## Fix
Add explicit propagation of the ``name`` attribute in the Modin implementation of ``Series.duplicated``.

```diff
diff --git a/modin/pandas/series.py b/modin/pandas/series.py
index 5c9e7a1..d3f4b2c 100644
--- a/modin/pandas/series.py
+++ b/modin/pandas/series.py
@@
     def duplicated(self, keep: str = "first") -> "Series":
         """
         Return Boolean Series denoting duplicate rows.
@@
-        return self.__constructor__(
-            self._query_compiler.duplicated(keep=keep)
-        )
+        # NOTE: ``Series.__constructor__`` creates a new ``Series`` from the
+        # underlying ``QueryCompiler`` result.  The constructor does **not**
+        # automatically copy the ``name`` attribute from ``self``.  In pandas,
+        # ``Series.duplicated`` returns a series that retains the original name,
+        # so we replicate that behaviour here by explicitly propagating the name.
+        result = self.__constructor__(
+            self._query_compiler.duplicated(keep=keep)
+        )
+        # Preserve the original series name (including ``None``) to match pandas.
+        result.name = self.name
+        return result
```

## How to verify
```python
>>> import modin.pandas as pd
>>> s = pd.Series([1, 2, 3, 2], name="a")
>>> dup = s.duplicated()
>>> dup
0    False
1    False
2    False
3     True
dtype: bool
>>> dup.name
'a'
>>> # also test the `keep` argument
>>> dup_last = s.duplicated(keep="last")
>>> dup_last.name
'a'
```

Run the Modin test suite (or at least the series tests) to ensure no regressions:

```bash
pytest modin/pandas/test_series.py::test_duplicated_name_preserved
```

The test should pass, confirming that the series name is now correctly preserved.
--- Proposal B
## Root cause
`Series.duplicated` in Modin delegates the operation to pandas via `_default_to_pandas`.  
The helper returns a **new** `Series` object but the implementation does not copy the
original series’ ``name`` attribute to the result. Consequently the returned series
has ``name = None`` while pandas correctly preserves the name.

## Fix
Add explicit propagation of the ``name`` attribute in the Modin implementation of
`Series.duplicated`.

```diff
--- a/modin/pandas/series.py
+++ b/modin/pandas/series.py
@@
     def duplicated(self, keep: str = "first") -> "Series":
         """
         Return Boolean Series denoting duplicate values.
@@
-        return self._default_to_pandas(pandas.Series.duplicated, keep=keep)
+        # NOTE: ``_default_to_pandas`` returns a new Series object that does **not**
+        # automatically inherit the ``name`` attribute from ``self``.  Pandas
+        # preserves the name, so we copy it explicitly to keep Modin behaviour
+        # consistent with pandas.
+        result = self._default_to_pandas(pandas.Series.duplicated, keep=keep)
+        # ``result`` is already a Modin ``Series`` (the wrapper created by
+        # ``_default_to_pandas``).  Propagate the original name if it is set.
+        if self.name is not None:
+            result.name = self.name
+        return result
```

### Explanation of the changes
1. Call the original pandas implementation via `_default_to_pandas` to obtain the
   duplicated boolean series.
2. After the call, explicitly set `result.name` to `self.name` when the original
   series has a name. This mirrors pandas’ behaviour.
3. Return the corrected `result`.

## How to verify
Run the following snippet both with the original Modin version and after applying the
patch:

```python
import modin.pandas as pd

# Series with a name
s = pd.Series([1, 2, 3, 2], name="a")
dup = s.duplicated()
print(dup)               # should be a boolean Series
print(dup.name)          # should output 'a'
```

Expected output:

```
0    False
1    False
2    False
3     True
dtype: bool
a
```

The test should also pass the equivalent pandas check:

```python
import pandas as pd
assert pd.Series([1,2,3,2], name="a").duplicated().name == "a"
```

Running the Modin test suite (e.g., `pytest modin/pandas/test_series.py::test_duplicated_name`)
should now succeed, confirming that the name is correctly preserved.

=== TASK urlaubsverwaltung__urlaubsverwaltung-4858 | repo urlaubsverwaltung/urlaubsverwaltung | java
--- Issue
Überstundenabbau über Api abfragbar

**Is your feature request related to a problem? Please describe.**
Damit Mitarbeiter kummuliert über einen bestimmten Zeitraum wissen, wie viele Überstunden sie abgebaut haben, braucht das Tool die Anzahl an Stunden/Minuten welche in einer Abwesenheit  vom Type `Überstundenabbau`  konfiguriert wurden, in der API.

**Describe the solution you'd like**
Da aktuell die Api `/api/persons/{personId}/absences` schon benutzt wird, wäre es dufte, wenn diese Information in diese API mitgegeben wird. Es könnte z.B. in einem optionalem Feld `hours` oder `duration` eingeführt werden. Dafür könnte `java.time.Duration` verwendet werden. 

**Optionally describe alternatives you've considered**
Eine separate API in der Details zu einem bestimmten Abwesenheit abgefragt werden können. Das würde funktionieren, jedoch müsste das Tool dann für jede Abwesenheit vom Typ `Überstundenabbau` nochmal separat die Stunden/Minuten abfragen. Das ist in meinen Augen ineffizienter, aber dafür wäre die API "sauberer".


--- REAL FIX (ground truth)
diff --git a/src/main/java/org/synyx/urlaubsverwaltung/absence/AbsenceApiController.java b/src/main/java/org/synyx/urlaubsverwaltung/absence/AbsenceApiController.java
index 82daab6ad..2d522fb74 100644
--- a/src/main/java/org/synyx/urlaubsverwaltung/absence/AbsenceApiController.java
+++ b/src/main/java/org/synyx/urlaubsverwaltung/absence/AbsenceApiController.java
@@ -14,6 +14,7 @@ import org.springframework.web.bind.annotation.RequestParam;
 import org.springframework.web.bind.annotation.RestController;
 import org.springframework.web.server.ResponseStatusException;
 import org.synyx.urlaubsverwaltung.api.RestControllerAdviceMarker;
+import org.synyx.urlaubsverwaltung.application.vacationtype.VacationCategory;
 import org.synyx.urlaubsverwaltung.period.DayLength;
 import org.synyx.urlaubsverwaltung.person.Person;
 import org.synyx.urlaubsverwaltung.person.PersonService;
@@ -25,6 +26,8 @@ import java.util.function.Predicate;
 import java.util.stream.Stream;
 
 import static org.springframework.hateoas.MediaTypes.HAL_JSON_VALUE;
+import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.linkTo;
+import static org.springframework.hateoas.server.mvc.WebMvcLinkBuilder.methodOn;
 import static org.springframework.http.HttpStatus.BAD_REQUEST;
 import static org.springframework.http.MediaType.APPLICATION_JSON_VALUE;
 import static org.synyx.urlaubsverwaltung.absence.AbsenceDto.AbsenceType.NO_WORKDAY;
@@ -160,7 +163,14 @@ public class AbsenceApiController {
     private AbsenceDto toAbsenceDto(LocalDate date, DayLength dayLength, AbsencePeriod.RecordInfo recordInfo) {
         final AbsenceDto.AbsenceType type = toAbsenceTypes(recordInfo.getAbsenceType());
         final String status = recordInfo.getStatus().name();
-        return new AbsenceDto(date, type, recordInfo.getId().orElse(null), status, dayLength, recordInfo.getCategory().orElse(null), recordInfo.getTypeId().orElse(null));
+        final String category = recordInfo.getCategory().orElse(null);
+        AbsenceDto absenceDto = new AbsenceDto(date, type, recordInfo.getId().orElse(null), status, dayLength, category, recordInfo.getTypeId().orElse(null));
+
+        Optional<Long> absenceId = recordInfo.getId();
+        if (VacationCategory.OVERTIME.name().equals(category) && absenceId.isPresent()) {
+            absenceDto.add(linkTo(methodOn(OvertimeAbsenceApiController.class).overtimeAbsence(recordInfo.getPerson().getId(), absenceId.get())).withRel("overtime"));
+        }
+        return absenceDto;
     }
 
     private List<AbsenceDto.AbsenceType> toAbsenceTypes(List<String> dayAbsenceTypes) {
diff --git a/src/main/java/org/synyx/urlaubsverwaltung/absence/OvertimeAbsenceApiController.java b/src/main/java/org/synyx/urlaubsverwaltung/absence/OvertimeAbsenceApiController.java
new file mode 100644
index 000000000..2bf5988c3
--- /dev/null
+++ b/src/main/java/org/synyx/urlaubsverwaltung/absence/OvertimeAbsenceApiController.java
@@ -0,0 +1,92 @@
+package org.synyx.urlaubsverwaltung.abs
--- Proposal A
## Root cause
The endpoint **`GET /api/persons/{personId}/absences`** returns a list of `AbsenceDto` objects.  
For absences of type **`Überstundenabbau`** (overtime reduction) the underlying domain object (`OvertimeReduction`) already stores the amount of time to be reduced as a `java.time.Duration`, but the DTO does not contain a field for this value and the mapper never copies it. Consequently the API cannot provide the number of hours/minutes that were reduced.

## Fix
* Add an optional `Duration duration` field to `AbsenceDto`.
* Extend the mapper (`AbsenceMapper`) to set this field when the source is an `OvertimeReduction`.
* Adjust JSON serialization (Jackson already handles `Duration` as an ISO‑8601 string) and update the Swagger/OpenAPI annotation (if present).

```diff
diff --git a/src/main/java/org/synyx/urlaubsverwaltung/api/AbsenceDto.java b/src/main/java/org/synyx/urlaubsverwaltung/api/AbsenceDto.java
index e3b7c1a..f9a2d4b 100644
--- a/src/main/java/org/synyx/urlaubsverwaltung/api/AbsenceDto.java
+++ b/src/main/java/org/synyx/urlaubsverwaltung/api/AbsenceDto.java
@@
 import java.time.LocalDate;
 import java.time.DayOfWeek;
+import java.time.Duration;
 import java.util.Optional;
 
 /**
  * DTO used by the REST API to transfer absence information.
  */
 public class AbsenceDto {
@@
     private final AbsenceType type;
 
     private final Optional<String> status;
 
-    // other fields …
+    /**
+     * Only set for absences of type {@code OVERTIME_REDUCTION}. Represents the amount of overtime that
+     * has been reduced. The value is expressed as a {@link java.time.Duration} and is serialized by Jackson
+     * as an ISO‑8601 string (e.g. {@code "PT8H30M"}).
+     */
+    private final Optional<Duration> duration;
 
     public AbsenceDto(Long id,
                       LocalDate startDate,
                       LocalDate endDate,
                       DayLength dayLength,
                       AbsenceType type,
-                      Optional<String> status) {
+                      Optional<String> status,
+                      Optional<Duration> duration) {
         this.id = id;
         this.startDate = startDate;
         this.endDate = endDate;
         this.dayLength = dayLength;
         this.type = type;
         this.status = status;
+        this.duration = duration;
     }
 
@@
     public Optional<String> getStatus() {
         return status;
     }
 
+    public Optional<Duration> getDuration() {
+        return duration;
+    }
+
     // equals, hashCode, toString …
 }
diff --git a/src/main/java/org/synyx/urlaubsverwaltung/api/AbsenceMapper.java b/src/main/java/org/synyx/urlaubsverwaltung/api/AbsenceMapper.java
index 7c9a1f2..d4e3b8a 100644
--- a/src/main/java/org/synyx/urlaubsverwaltung/api/AbsenceMapper.java
+++ b/src/main/java/org/synyx/urlaubsverwaltung/api/AbsenceMapper.java
@@
 import org.synyx.urlaubsverwaltung.overtime.Overtime;
 import org.synyx.urlaubsverwaltung.overtime.OvertimeReduction;
 import org.synyx.urlaubsverwaltung.person.Person;
@@
 public final class AbsenceMapper {
 
     private AbsenceMapper() {
         // util
     }
 
     public static AbsenceDto mapToDto(Object absence) {
-        // existing mapping logic …
+        if (absence instanceof Application) {
+            Application application = (Application) absence;
+            return new AbsenceDto(
+                application.getId(),
+                application.getStartDate(),
+                application.getEndDate
--- Proposal B
## Root cause
The public API `/api/persons/{personId}/absences` returns a list of absences but does **not** expose the configured amount of time for absences of type **Überstundenabbau** (overtime‑reduction).  
The underlying `Absence` entity already stores this value (as a `java.time.Duration` or a numeric hour/minute field), but the DTO that is sent to the client (`AbsenceDto`) does not contain a corresponding property, therefore the information cannot be retrieved via the API.

## Fix
Add an optional `Duration duration` field to the DTO and map it from the entity.  
The field is only filled for absences of type `OVERTIME_REDUCTION`; for all other types it stays `null` and is omitted from the JSON output.

```diff
--- a/src/main/java/org/urlaubsverwaltung/absence/web/AbsenceDto.java
+++ b/src/main/java/org/urlaubsverwaltung/absence/web/AbsenceDto.java
@@
 import com.fasterxml.jackson.annotation.JsonInclude;
 import java.time.LocalDate;
+import java.time.Duration;
 import java.util.List;
@@
 @JsonInclude(JsonInclude.Include.NON_NULL)
 public class AbsenceDto {
 
     private Long id;
@@
     private String type;
 
+    /**
+     * Only set for absences of type {@code OVERTIME_REDUCTION}. Represents the amount of overtime that is
+     * reduced by this absence (e.g. {@code PT2H30M} for 2 hours 30 minutes). For all other absence types the
+     * field stays {@code null} and is omitted from the JSON payload.
+     */
+    private Duration duration;
@@
     private List<AbsenceCommentDto> comments;
 
@@
     public String getType() {
         return type;
     }
 
     public void setType(String type) {
         this.type = type;
     }
+
+    public Duration getDuration() {
+        return duration;
+    }
+
+    public void setDuration(Duration duration) {
+        this.duration = duration;
+    }
 
     public List<AbsenceCommentDto> getComments() {
         return comments;
     }
```

```diff
--- a/src/main/java/org/urlaubsverwaltung/absence/web/AbsenceMapper.java
+++ b/src/main/java/org/urlaubsverwaltung/absence/web/AbsenceMapper.java
@@
 import org.urlaubsverwaltung.absence.Absence;
 import org.urlaubsverwaltung.absence.AbsenceComment;
 import org.urlaubsverwaltung.absence.AbsenceType;
+import java.time.Duration;
 
 public class AbsenceMapper {
 
     public static AbsenceDto mapToDto(Absence absence) {
         AbsenceDto dto = new AbsenceDto();
@@
         dto.setType(absence.getType().name());
 
+        // Only expose the configured duration for overtime‑reduction absences.
+        if (absence.getType() == AbsenceType.OVERTIME_REDUCTION) {
+            Duration duration = absence.getDuration(); // the entity already stores a Duration
+            dto.setDuration(duration);
+        }
+
         dto.setComments(mapComments(absence.getComments()));
 
         return dto;
     }
```

*If the `Absence` entity does not yet have a `Duration getDuration()` method, add it (and the corresponding field) in the entity class. The change below is only required if the field is missing.*

```diff
--- a/src/main/java/org/urlaubsverwaltung/absence/Absence.java
+++ b/src/main/java/org/urlaubsverwaltung/absence/Absence.java
@@
 import jakarta.persistence.*;
 import java.time.LocalDate;
+import java.time.Duration;
 
 @Entity
 public class Absence {
@@
     @Column(name = "type", nullable = false)
     @Enumerated(EnumType.STRING)
     private AbsenceType type;
 
+    /**
+     * Only used when {@link #type} is {@code OVERTIME_REDUCTION}. Stores 

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
The parser treats string literals as a single token and never looks inside the quotes. Therefore an expression like `"Hello #{name}"` is read as a plain string and the `#{}` interpolation is never recognized. Because no AST node exists for interpolated strings, the evaluator and type‑checker also have no way to handle the embedded expression, resulting in a syntax error or a plain literal that does not contain the interpolated value.

## Fix
* Add a new AST node `InterpolatedString` that stores a list of parts (plain text strings or sub‑expressions).
* Extend the string literal parser to recognise `#{ … }` blocks, parse the inner expression, and build a list of parts.
* Update the evaluator to evaluate each part, enforce that every evaluated part is a string, and concatenate the results.
* Update the type‑checker to type‑check each part, require the type `String` for every interpolated expression, and infer the whole expression as `String`.

```diff
diff --git a/lib/fika/ast.ex b/lib/fika/ast.ex
index 3a1f9c2..e7b4d8a 100644
--- a/lib/fika/ast.ex
+++ b/lib/fika/ast.ex
@@
   defstruct [:value]                     # literals (int, string, bool, …)
   # … other AST structs …
 
+  # ----------------------------------------------------------------------
+  # Interpolated string
+  #   parts – list where each element is either a binary (plain text) or an
+  #           AST node representing an expression to be evaluated and
+  #           concatenated.
+  # ----------------------------------------------------------------------
+  defmodule InterpolatedString do
+    @moduledoc false
+    defstruct [:parts]
+  end
+
   # Helper to make pattern matching a bit nicer
   defmacro __using__(_opts) do
     quote do
       alias Fika.AST.{Literal, Variable, Call, Function, Block, InterpolatedString}
     end
   end
 end
```

```diff
diff --git a/lib/fika/parser/literal_exps.ex b/lib/fika/parser/literal_exps.ex
index 5c2d7a1..b9e3f4c 100644
--- a/lib/fika/parser/literal_exps.ex
+++ b/lib/fika/parser/literal_exps.ex
@@
   use NimbleParsec
   alias Fika.Parser.{Helper, Expression}
   alias Fika.AST.{Literal, InterpolatedString}
 
-  # plain string literal, no interpolation
-  string_literal =
-    ignore(string("\""))
-    |> repeat(
-         choice([
-           string("\\\"") |> replace("\""),
-           utf8_char(not_one_of(~c("\"")))
-         ])
-       )
-    |> ignore(string("\""))
-    |> reduce({Enum, :join, []})
-    |> map({Literal, :new, [:string]})
+  # ----------------------------------------------------------------------
+  # Interpolated string literal
+  #   - plain text fragments are kept as binaries
+  #   - #{ expr } fragments are parsed as full expressions
+  #   - the resulting list of parts is wrapped in %InterpolatedString{}
+  # ----------------------------------------------------------------------
+  escaped_quote =
+    string("\\\"") |> replace("\"")
+
+  # any character that is not a quote or a # (which could start an interpolation)
+  plain_char =
+    utf8_char(not_one_of(~c("\"#")))
+
+  # #{ expression } block
+  interpolation =
+    ignore(string("#{"))
+    |> concat(Expression.expr())
+    |> ignore(string("}"))
+    |> tag(:interp)
+
+  string_part =
+    choice([
+      escaped_quote,
+      plain_char,
+      interpolation
+    ])
+
+  interpolated_string =
+    ignore(string("\""))
+    |> repeat(string_part)
+    |> ignore(string("\""))
+    |> reduce({Enum, :join, []})
+    |> map(fn parts ->
+  
--- Proposal B
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
`Message.IsValid` does not verify that the various string fields (bus names, object paths, member names, error names) conform to the D‑Bus specification.  
The encoder (`encoder.go`) already performs these checks when it builds a message, but the validation routine used by callers (e.g. `Conn.Emit`) only checks for non‑empty fields. Consequently an application can construct a malformed `Message` (wrong interface name, illegal signal/member name, etc.) that passes `IsValid` but later causes the daemon to disconnect the whole connection when the message is encoded and sent.

## Fix
* Extend the validation helpers (`isValidBusName`, `isValidMember`, `isValidObjectPath`) and expose them from `name.go`.
* Update `Message.IsValid` to use the new helpers and also validate error names.
* Add a check in `Conn.Emit` that calls `msg.IsValid` before encoding and returns a descriptive error instead of sending an invalid message.
* Keep the existing encoder checks untouched – they now have a matching counterpart in `IsValid`.

```diff
diff --git a/name.go b/name.go
index e69de29..b1c2f3a 100644
--- /dev/null
+++ b/name.go
@@
+package dbus
+
+import (
+	"regexp"
+)
+
+// Regular expressions taken from the D‑Bus specification.
+var (
+	// Bus names (including well‑known names, unique names and interface names)
+	busNameRE = regexp.MustCompile(`^([A-Za-z_][A-Za-z0-9_]*\.)*[A-Za-z_][A-Za-z0-9_]*$`)
+
+	// Member names (signal or method names)
+	memberNameRE = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)
+
+	// Object paths
+	objectPathRE = regexp.MustCompile(`^(/([A-Za-z_][A-Za-z0-9_]*))*$`)
+)
+
+// isValidBusName reports whether s is a valid D‑Bus bus name (including
+// interface names and error names).  The empty string is considered invalid.
+func isValidBusName(s string) bool {
+	return busNameRE.MatchString(s)
+}
+
+// isValidMember reports whether s is a valid D‑Bus member name.
+func isValidMember(s string) bool {
+	return memberNameRE.MatchString(s)
+}
+
+// isValidObjectPath reports whether s is a valid D‑Bus object path.
+func isValidObjectPath(s string) bool {
+	return objectPathRE.MatchString(s)
+}
+
+// isValidErrorName reports whether s is a valid D‑Bus error name.
+// An error name is just a bus name that starts with the reserved prefix
+// "org.freedesktop.DBus.Error." (the prefix itself is also a valid bus name).
+func isValidErrorName(s string) bool {
+	if !isValidBusName(s) {
+		return false
+	}
+	// The spec requires the error name to contain at least one dot after the
+	// "Error" component, but the generic bus‑name check already guarantees the
+	// overall structure, so we only need to ensure the prefix is present.
+	const errPrefix = "org.freedesktop.DBus.Error."
+	return len(s) > len(errPrefix) && s[:len(errPrefix)] == errPrefix
+}
+
diff --git a/message.go b/message.go
index 5d2c1a7..c3f9b4e 100644
--- a/message.go
+++ b/message.go
@@
 type Message struct {
@@
 }
 
 // IsValid reports whether the message is well‑formed according to the D‑Bus
 // specification.  It does **not** guarantee that the message can be encoded
 // (e.g. unsupported argument types are still considered valid here).
 func (msg *Message) IsValid() bool {
-	// Basic sanity checks – the type must be known and the serial must be
-	// non‑zero for messages that travel on the bus.
-	if msg.Type < TypeInvalid || msg.Type > TypeSignal {
-		return false
-	}
-	if msg.Type != TypeMethodCall && msg.Type != TypeMethodReturn && msg.Type != TypeError && m
--- Proposal B
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