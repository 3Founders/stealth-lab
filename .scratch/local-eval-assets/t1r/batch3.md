

=== TASK ucfopen__canvasapi-467 | repo ucfopen/canvasapi | python
--- Issue
Handle Rate Limit
When a developer gets a little overzealous and implements multiprocessing, or has many applications/instances of an application using the same access token, It's possible to hit a rate limit. When that happens, Canvas returns a 403 status code. It appears that the only way to distinguish between being rate limited and another type of 403 is the response content will be `403 Forbidden (Rate Limit Exceeded)`.

A new exception (`RateLimitExceeded`?) would help distinguish between when the developer is being rate limited and when they are being denied access for another reason. It would also be beneficial to include the value `X-Rate-Limit-Remaining` header in the exception to show how low the balance is.

Making the new exception a subclass of `Forbidden` might be nice for backwards compatibility where the developer may already be handling `Forbidden` for rate limiting cases.

[Canvas docs on rate limits and throttling](https://canvas.instructure.com/doc/api/file.throttling.html)
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 5fc59c7..b865df3 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -2,6 +2,10 @@
 
 ## [Unreleased]
 
+### General
+
+- Added `RateLimitExceeded` exception to distinguish between being rate limited and being otherwise forbidden from accesing a resource. It is a subclass of the `Forbidden` exception.
+
 ## [2.1.0] - 2020-12-04
 
 ### New Endpoint Coverage
diff --git a/README.md b/README.md
index d8ddfe7..f4d0c1b 100644
--- a/README.md
+++ b/README.md
@@ -171,4 +171,4 @@ If you have a project that uses CanvasAPI that you'd like to promote, please con
 
 ## Contact Us
 
-Need help? Have an idea? Just want to say hi? Come join us on the [UCF Open Slack Channel](https://ucf-open-slackin.herokuapp.com) and join the `#canvasapi` channel!
+Need help? Have an idea? Feel free to check out our [Discussions](https://github.com/ucfopen/canvasapi/discussions) board. Just want to say hi or get extended spport? Come join us on the [UCF Open Slack Channel](https://ucf-open-slackin.herokuapp.com) and join the `#canvasapi` channel!
diff --git a/canvasapi/exceptions.py b/canvasapi/exceptions.py
index 3e4d22f..2ab31a9 100644
--- a/canvasapi/exceptions.py
+++ b/canvasapi/exceptions.py
@@ -55,6 +55,15 @@ class Forbidden(CanvasException):
     pass
 
 
+class RateLimitExceeded(Forbidden):
+    """
+    Canvas has recieved to many requests from this access token and is
+    throttling this request. Try again later.
+    """
+
+    pass
+
+
 class Conflict(CanvasException):
     """Canvas had a conflict with an existing resource."""
 
diff --git a/canvasapi/requester.py b/canvasapi/requester.py
index 2d236ce..b1b8745 100644
--- a/canvasapi/requester.py
+++ b/canvasapi/requester.py
@@ -10,6 +10,7 @@ from canvasapi.exceptions import (
     Conflict,
     Forbidden,
     InvalidAccessToken,
+    RateLimitExceeded,
     ResourceDoesNotExist,
     Unauthorized,
     UnprocessableEntity,
@@ -241,7 +242,15 @@ class Requester(object):
             else:
 
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Warn users when making insecure HTTP POST requests
      Warn users that POST requests sent over non-HTTPS may fail
In some cases, Canvas's API responds unpredictably when sending POST requests over HTTP instead of HTTPS.

For example, the `account.create_course` call will return the `GET` response if made over HTTP instead of HTTPS. This breaks `Canvas
      Way: Warn users during initialization when base URL uses unencrypted HTTP
      - Locate the main client class initialization where the base URL is validated and normalized.
      - Identify where URL protocol checks occur during initialization.
      - Add a warning check that detects if 'http://' is present in the base URL and issues a UserWarning advising the use of HTTPS.
      - Run the test suite to verify that initializing the client with an HTTP URL emits a UserWarning.
  [2] Goal: Raise appropriate exceptions for HTTP 4xx and 5xx API responses
      Implement 503 Handling
Upon receiving a 503 response from Canvas, the library returns a `json.decoder.JSONDecodeError` instead of something more useful.

Implement handling for 503 response.

It's probably worth implementing generic handling for all 4xx and 5xx responses.
      Way: Catch unhandled 4xx and 5xx HTTP error status codes with generic exceptions
      - Locate HTTP response status handling in the central API requester/client layer.
      - Identify status codes that are not explicitly handled (such as 503 or arbitrary 4xx/5xx errors) and cause parsing crashes like JSONDecodeError when downstream expects JSON.
      - Replace explicit single-status checks like status_code == 500 with a broader condition (status_code > 400) after all specific status handlers, raising a generic library exception containing the response status code.
      - Run test suite to verify unhandled 4xx and 5xx error responses raise the generic library exception.
  [3] Goal: Add support for file  usage rights endpoints
      Usage Rights (Files)
I would like to set usage rights (published, etc) for specific files. It seems [Files API/set_usage_rights](https://canvas.instructure.com/doc/api/files.html#method.usage_rights.set_usage_rights) should do it, but I haven't been able to figure out how to do this with canvasapi. 
      Way: implement file usage rights and license endpoints across context scopes
      - Inspect the Canvas API documentation for file usage rights endpoints (get licenses, set usage rights, remove usage rights) across Course, Group, and User scopes.
      - Create License and UsageRights model classes subclassing CanvasObject with __str__ representations.
      - Implement get_licenses (returns PaginatedList of License), set_usage_rights (PUT returning UsageRights), and remove_usage_rights (DELETE returning JSON) methods on Course, Group, and User classes.
      - Add unit tests mocking requests for get_licenses, set_usage_rights, and remove_usage_rights on Course, Group, and User, plus __str__ tests for the new models.
      - Run the test suite to verify the new endpoints and models work properly.

=== TASK bitshifter__glam-rs-187 | repo bitshifter/glam-rs | rust
--- Issue
".fract" for vector types
(Minor convenience thing, absolutely not urgent in any way).

In GLSL, fract is quite commonly used, in things like this hash function in https://www.shadertoy.com/view/tlGfzd:

```glsl
// by Dave_Hoskins
float hash12(vec2 p)
{
    vec3 p3  = fract(vec3(p.xyx) * .1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
```

It just returns the fractional value, or `x - floor(x)`:  (https://www.khronos.org/registry/OpenGL-Refpages/gl4/html/fract.xhtml).

Having this in glam would be convenient when translating GLSL shaders to rust-gpu, for example.
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index b2be6d4..e323c93 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -9,6 +9,8 @@ The format is based on [Keep a Changelog], and this project adheres to
 
 ### Added
 
+* Added `fract()` method to float vector types which return a vector containing
+  `self - self.floor()`.
 * Added optional support for the `approx` crate. Note that all glam types
   implement their own `abs_diff_eq()` method without requiring the `approx`
   dependency.
diff --git a/src/core/traits/scalar.rs b/src/core/traits/scalar.rs
index fc1a399..1b2c18e 100644
--- a/src/core/traits/scalar.rs
+++ b/src/core/traits/scalar.rs
@@ -278,8 +278,7 @@ impl FloatEx for f64 {
     }
     #[inline(always)]
     fn acos_approx(self) -> Self {
-        // TODO: clamp range
-        f64::acos(self)
+        f64::acos(self.max(-1.0).min(1.0))
     }
 }
 
@@ -383,4 +382,8 @@ fn test_scalar_acos() {
     // input is clamped to -1.0..1.0
     assert_approx_eq!(2.0_f32.acos_approx(), 0.0);
     assert_approx_eq!((-2.0_f32).acos_approx(), core::f32::consts::PI);
+
+    // input is clamped to -1.0..1.0
+    assert_eq!(2.0_f64.acos_approx(), 0.0);
+    assert_eq!((-2.0_f64).acos_approx(), core::f64::consts::PI);
 }
diff --git a/src/mat3.rs b/src/mat3.rs
index e961fb7..f71bf3f 100644
--- a/src/mat3.rs
+++ b/src/mat3.rs
@@ -29,7 +29,8 @@ macro_rules! define_mat3_struct {
         ///
         /// This 3x3 matrix type features convenience methods for creating and using linear and
         /// affine transformations. If you are primarily dealing with 2D affine transformations the
-        /// [`Affine2`] type is much faster and more space efficient than using a 3x3 matrix.
+        /// [`Affine2`][crate::Affine2] type is much faster and more space efficient than using a
+        /// 3x3 matrix.
         ///
         /// Linear transformations including 3D rotation and scale can be created using methods
         /// such as [`Self::from_diagonal()`], [`Self::from_quat()`], [`Sel
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Add scalar linear interpolation functions for floating point types
      lerp over scalar types?
Hello!

I'm using glam via bevy. Ran into a situation where I need to lerp over a single f32 value i.e. non-vector. I couldn't find a function for it in glam, so I would either need use Vec2::lerp and de-structure the single value out of it, or write my own boilerplate code
      Way: Implement scalar linear interpolation extension traits via templates
      - Define a public `FloatExt` trait containing interpolation methods such as `lerp`, `inverse_lerp`, and `remap`.
      - Create a code generation template implementing `FloatExt` for scalar float types (`f32`, `f64`) and register it in the codegen generator configuration.
      - Expose the `FloatExt` trait and its module implementations in root crate modules (`lib.rs`, `f32.rs`, `f64.rs`).
      - Run code generation or generate output files and execute tests across float types.
  [2] Goal: Provide functions to compute Euclidean distance and squared distance between two points
      Distance between two points
It would be nice to implement two functions `distance` and `distance_squared` which returns the Euclidean distance between two points. In other words, `distance` would return `sqrt((x1-x2)^2 + (y1-y2)^2 + (z1-z2)^2)` while `distance_squared` would return `(x1-x2)^2 + (y1-
      Way: Add Euclidean distance methods to vector types
      - Locate the implementations of Vec2, Vec3, Vec3A, and Vec4 in the source tree and confirm they already provide length() and length_squared() methods.
      - Edit each vector impl to add a public inline `distance` method that returns (self - other).length() and a `distance_squared` method that returns (self - other).length_squared(). Include brief doc comments.
      - Run the full test suite to verify that the new methods compile and do not break existing behaviour.

=== TASK goadesign__goa-2708 | repo goadesign/goa | go
--- Issue
Non Required attributes with Default/Enum creating error
```
package design

var Storedload = ResultType("application/vnd.test", func() {
	TypeName("Storedload")

	Attributes(func() {
		Attribute("uploaded", Boolean, func() {
			Default(true)
		})
		//Required("uploaded")
	})

	View("default", func() {
		Attribute("uploaded")
	})

})
```
If I comment Required with Default value then it's creating below code. That has a type mismatch error.
```
{
		var zero bool
		if vres.Uploaded == zero {
			vres.Uploaded = true
		}
	}
```
--- Real fix (for your judgement only)
diff --git a/codegen/go_transform.go b/codegen/go_transform.go
index a033158f..0e8aeda7 100644
--- a/codegen/go_transform.go
+++ b/codegen/go_transform.go
@@ -236,7 +236,7 @@ func transformObject(source, target *expr.AttributeExpr, sourceVar, targetVar st
 		// Default value handling. We need to handle default values if the target
 		// type uses default values (i.e. attributes with default values are
 		// non-pointers) and has a default value set.
-		if tdef := tgtMatt.GetDefault(n); tdef != nil && ta.TargetCtx.UseDefault && !srcMatt.IsRequired(n) {
+		if tdef := tgtMatt.GetDefault(n); tdef != nil && ta.TargetCtx.UseDefault && !ta.TargetCtx.Pointer && !srcMatt.IsRequired(n) {
 			switch {
 			case ta.SourceCtx.IsPrimitivePointer(n, srcMatt.AttributeExpr) || !expr.IsPrimitive(srcc.Type):
 				// source attribute is a primitive pointer or not a primitive

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Check for empty string instead of nil when validating required body attributes
      When add String value to Required attribute in body in html generated code check if string == nil and this not acceptable in go cause string have zero value 
Design :

```  
var _ = Service("foo", func() {

	Method("bar", func() {

		Payload(func() {
			Field(1, "user-id", String, "User Id")
      Way: Enforce HTTP body required attributes match method payload requirements
      - Inspect endpoint validation logic where HTTP request body expressions are checked against service method payload definitions.
      - In HTTPEndpointExpr.Validate, add a check ensuring all attributes marked Required in the HTTP Body validation list are also marked Required in the method Payload validation list, reporting validation errors for any discrepancies.
      - Update CLI and server constructors to properly handle pointer referencing when assigning primitive return type attributes in generated payload builders.
      - Run endpoint validation tests to confirm missing required body attributes are reported.
  [2] Goal: Fix generated code assigning pointer string to string
      v3: client/encode_decode.go has a type mismatch error
I was generating my code, and I am getting a weird type mismatch error.

in the `client/encode_decode.go` for a service, the `BuildUpdateRequest` tries to set a `*string` into a `string`

```go
func (c *Client) BuildUpdateRequest(ctx context
      Way: Ensure generated path parameters reflect payload pointer status for object payloads
      - Locate the generated client code responsible for building HTTP requests, specifically where path parameters are assigned.
      - Understand that the type mismatch occurs because the generated code incorrectly assumes a path parameter is non-pointer (`string`) when its source in the method's payload is a pointer (`*string`), specifically when the method's payload is an object.
      - Identify the code generation logic that determines whether a path parameter should be treated as a pointer type in the generated code.
      - Modify the code generation logic to explicitly check the method's payload for the primitive pointer status of a path parameter when the method's payload is an object. This ensures the generated type correctly reflects the payload's definition.
      - Regenerate the code and run the relevant tests to confirm the type mismatch is resolved and the generated code compiles and behaves as expected.
  [3] Goal: Ensure JWT token attribute is recognized in recursive type extensions
      Extending recursively does not define JWT token
Extending a type that defines token works but extending a type that extends a type that defines token does not work. Example:
`$GOPATH/src/foo/design/api.go`
```
package design

import (
	. "goa.design/goa/v3/dsl"
)

var JWTAuth = JWTSecurity(
      Way: Enable recursive attribute lookup for extended types
      - Identify the component responsible for resolving attributes in extended types, specifically `TaggedAttribute` in `expr/attribute.go`.
      - Modify the `TaggedAttribute` function to correctly handle `UserType` bases during recursive attribute lookup. When iterating through `a.Bases`, if a base `b` is a `UserType`, ensure that the recursive call to `TaggedAttribute` uses the `AttributeExpr` of that `UserType` (`ut.Attribute()`) instead of just creating a new `AttributeExpr` with `Type: b`.
      - Refactor the `Unique` name generation logic in `codegen/scope.go` to ensure consistent and correct unique name assignment, especially when suffixes are involved.
      - Update the HTTP code generation to use the improved unique name scoping for client arguments in request builders, ensuring argument names are unique within their scope.
      - Run the provided tests to confirm the fix addresses the recursive extension issue and does not introduce regressions.

=== TASK wikidata__wikidata-toolkit-748 | repo Wikidata/Wikidata-Toolkit | java
--- Issue
JSON dump parsing error while running examples
I encountered below error message when running the examples `GreatestNumberProcessor` and `GenderRatioProcessor`.   
```
Problematic line was: {"type":"lexeme","id":"L34069","lemmas":{"de":{"la...
Error when reading JSON for entity: Missing type id when trying to resolve subtype of [simple type, class org.wikidata.wdtk.datamodel.implementation.FormDocumentImpl]: missing type id property 'type' (for POJO property 'forms')
 at [Source: (String)"{"type":"lexeme","id":"L34070","lemmas":{"de":{"language":"de","value":"Zahnl\u00fccke"}},"lexicalCategory":"Q1084","language":"Q188","claims":{"P5185":[{"mainsnak":{"snaktype":"value","property":"P5185","datavalue":{"value":{"entity-type":"item","numeric-id":1775415,"id":"Q1775415"},"type":"wikibase-entityid"},"datatype":"wikibase-item"},"type":"statement","id":"L34070$B9D7523B-8AE6-4C39-85A2-EE7CC71C5228","rank":"normal"}],"P8376":[{"mainsnak":{"snaktype":"value","property":"P8376","datavalue""[truncated 4073 chars]; line: 1, column: 1043] (through reference chain: org.wikidata.wdtk.datamodel.implementation.LexemeDocumentImpl["forms"]->java.util.ArrayList[0])
```
The dump is automatically downloaded `20210407.json.gz`.  
  
I tried to run the code on Windows (Build with Maven with IDEA, JDK 15.0.1) and Ubuntu (openJDK 1.8.0_282, run as IDEA remote). 

It seems the dump file size is only ~200MB. Would that be correct?

Any help is appreciated. Thank you.
--- Real fix (for your judgement only)
diff --git a/pom.xml b/pom.xml
index 3f1a0c00..acc7b28f 100644
--- a/pom.xml
+++ b/pom.xml
@@ -80,7 +80,7 @@
 		<hamcrestVersion>2.2</hamcrestVersion>
 		<mockitoVersion>4.9.0</mockitoVersion>
 		<rdf4jVersion>3.7.7</rdf4jVersion>
-		<slf4jVersion>2.0.5</slf4jVersion>
+		<slf4jVersion>2.0.6</slf4jVersion>
 		<threetenVersion>1.7.1</threetenVersion>
 		<okhttpSignpostVersion>1.1.0</okhttpSignpostVersion>
 		<okhttpVersion>4.10.0</okhttpVersion>
diff --git a/wdtk-datamodel/src/main/java/org/wikidata/wdtk/datamodel/helpers/LexemeDeserializer.java b/wdtk-datamodel/src/main/java/org/wikidata/wdtk/datamodel/helpers/LexemeDeserializer.java
new file mode 100644
index 00000000..a4b4a0ec
--- /dev/null
+++ b/wdtk-datamodel/src/main/java/org/wikidata/wdtk/datamodel/helpers/LexemeDeserializer.java
@@ -0,0 +1,123 @@
+package org.wikidata.wdtk.datamodel.helpers;
+
+/*-
+ * #%L
+ * Wikidata Toolkit Data Model
+ * %%
+ * Copyright (C) 2014 - 2022 Wikidata Toolkit Developers
+ * %%
+ * Licensed under the Apache License, Version 2.0 (the "License");
+ * you may not use this file except in compliance with the License.
+ * You may obtain a copy of the License at
+ * 
+ *      http://www.apache.org/licenses/LICENSE-2.0
+ * 
+ * Unless required by applicable law or agreed to in writing, software
+ * distributed under the License is distributed on an "AS IS" BASIS,
+ * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
+ * See the License for the specific language governing permissions and
+ * limitations under the License.
+ * #L%
+ */
+
+import com.fasterxml.jackson.core.JacksonException;
+import com.fasterxml.jackson.core.JsonParser;
+import com.fasterxml.jackson.core.JsonProcessingException;
+import com.fasterxml.jackson.core.type.TypeReference;
+import com.fasterxml.jackson.databind.DeserializationContext;
+import com.fasterxml.jackson.databind.JsonMappingException;
+import com.fasterxml.jackson.databind.JsonNode;
+import com.fasterxml.jackson.databind.deser.std.
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support deserializing properties with mixed-case datatypes
      Deserializing a property with `localMedia` datatype fails
When trying to deserialize a property with `localMedia` dataype, such as this one:

```json
        {
            "pageid": 6,
            "ns": 122,
            "title": "Property:P3",
            "lastrevid": 9,
            "modifie
      Way: preserve mixed-case JSON datatypes during entity deserialization
      - Examine datatype validation regex and mapping methods where JSON datatypes are parsed into IRI representations.
      - Allow uppercase letters in JSON datatype regex validation so mixed-case names (such as camelCase datatypes) pass validation.
      - Add explicit JSON string storage to datatype value model and interface so the original JSON string representation is retained alongside the IRI.
      - Update property document deserializer and factory classes to construct datatype values using both the resolved IRI and original JSON string.
      - Update equality and string representation helpers to incorporate the stored datatype JSON string representation.
      - Run deserialization and model test suites to verify mixed-case datatypes deserialize and round-trip successfully.
  [2] Goal: Return revision ID on successful entity edit
      Editing API: return revision id on successful edit
The main method to make edits to existing entities looks like this:
```java
public void editEntityDocument(
	EntityUpdate update, boolean clear, String summary, List<String> tags)
        throws IOException, MediaWikiApiErrorException;
```
It 
      Way: Return revision ID from successful Wikibase API edits
      - Locate the `editEntityDocument` method in `WikibaseDataEditor` that performs entity edits and currently returns `void`.
      - Create a new data class, `EditingResult`, to encapsulate the outcome of an edit, specifically holding the `revisionId` as a `long` and providing an `OptionalLong` accessor for it.
      - Change the return type of `editEntityDocument` from `void` to the newly created `EditingResult` class.
      - Introduce a private helper method, `getRevisionIdFromResponse`, to parse the JSON response from `wbEditingAction` calls and extract the revision ID. This method should handle cases where the revision ID might be missing or malformed.
      - Modify all internal calls to `wbEditingAction` methods (e.g., `wbSetClaim`, `wbRemoveClaims`, `wbSetLabel`) to capture their `JsonNode` return value. Then, use the `getRevisionIdFromResponse` helper to extract the revision ID and return a new `EditingResult` object containing it.
      - Replace any `return;` statements within `editEntityDocument` that signify no actual edit was performed (e.g., when the update is empty) with `return new EditingResult(0L);` to indicate no revision ID was generated.

=== TASK eslint__doctrine-182 | repo eslint/doctrine | js
--- Issue
Support type expression for @this tag
See #143 

The current implementation only supports one name in the expression:
```js
/**
 * @this {Widget}
 */
```

UnionType of NameExpression should be supported as well:
```js
/**
 * @this {Widget|Button}
 */
```
--- Real fix (for your judgement only)
diff --git a/lib/doctrine.js b/lib/doctrine.js
index 095eeeb..a5804df 100644
--- a/lib/doctrine.js
+++ b/lib/doctrine.js
@@ -10,13 +10,11 @@
 
     var typed,
         utility,
-        isArray,
         jsdoc,
         esutils,
         hasOwnProperty;
 
     esutils = require('esutils');
-    isArray = require('isarray');
     typed = require('./typed');
     utility = require('./utility');
 
@@ -268,7 +266,7 @@
 
         function scanIdentifier(last) {
             var identifier;
-            if (!esutils.code.isIdentifierStartES5(source.charCodeAt(index))) {
+            if (!esutils.code.isIdentifierStartES5(source.charCodeAt(index)) && !source[index].match(/[0-9]/)) {
                 return null;
             }
             identifier = advance();
@@ -296,13 +294,13 @@
                 return null;
             }
 
-            if (allowBrackets && source.charCodeAt(index) === 0x5B  /* '[' */) {
-                useBrackets = true;
-                name = advance();
-            }
-
-            if (!esutils.code.isIdentifierStartES5(source.charCodeAt(index))) {
-                return null;
+            if (source.charCodeAt(index) === 0x5B  /* '[' */) {
+                if (allowBrackets) {
+                    useBrackets = true;
+                    name = advance();
+                } else {
+                    return null;
+                }
             }
 
             name += scanIdentifier(last);
@@ -525,8 +523,8 @@
                         // extract the default value if there is one
                         // example: @param {string} [somebody=John Doe] description
                         assign = name.substring(1, name.length - 1).split('=');
-                        if (assign[1]) {
-                            this._tag['default'] = assign[1];
+                        if (assign.length > 1) {
+                            this._tag['default'] = assign.slice(1).join('=');
                         }
                         this._tag.name = a
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow `@returns` tag without type annotation
      Allow omission of type in returns tag
Currently doctrine permits syntax like

```js
/**
 * @param foo - this is a param
 */
```

But not the same for return values

```js
/**
 * @returns foo - this is a return value
 */
```

Having both tags with optional type annotations would be gr
      Way: Allow optional type annotation for JSDoc @returns tags
      - Understand that JSDoc `@returns` tags currently enforce a type annotation, unlike `@param` tags, leading to parsing errors when a type is omitted.
      - Locate the code responsible for determining if a JSDoc tag requires a type parameter and where errors for missing types are generated.
      - Introduce a new helper function, `isReturnTitle`, to consolidate checks for 'return' and 'returns' tag titles.
      - Modify the `isTypeParameterRequired` function to use the new `isReturnTitle` helper, ensuring that 'return' and 'returns' are no longer explicitly listed as requiring a type parameter in this function's logic.
      - Adjust the error condition within the `_parseTag` method that checks for missing or invalid tag types. Extend the condition to also exclude return-related tags (identified by `isReturnTitle`) from triggering a 'Missing or invalid tag type' error if no type is found.
      - Verify that the changes correctly allow `@returns` tags without a type annotation and do not introduce regressions.
  [2] Goal: Make JSDoc parser report correct tag line numbers and descriptions
      Reported line numbers are sometimes incorrect
```
let comment = `
/**
 * Test class
 * @extends {BaseClass}
 * @constructor
 * @param {string} name
 */
`;

doctrine.parse(comment, {unwrap: true, lineNumbers: true});


{ description: '/**\nTest class',
  tags: 
   [ { title: 'extends',
      Way: Correct JSDoc tag line number and description parsing
      - Analyze the provided JSDoc comment and the `doctrine.parse` output to identify discrepancies in `lineNumber` and `description` for tags, noting that line numbers are off and descriptions can be malformed.
      - Locate the code responsible for incrementing line numbers during comment parsing and the logic for advancing the global parser's position after a tag has been processed.
      - Remove the premature line number increment within the comment parsing loop to prevent line numbers from advancing incorrectly for every line terminator encountered inside a comment block.
      - Modify the global parser's index advancement after a tag is parsed. Replace the direct assignment of `index = this._last;` with an explicit loop that calls `advance()` until the global index matches the tag parser's end position. This ensures the global parser correctly catches up to the end of the just-parsed tag, preventing misinterpretation of subsequent content.
      - Run the relevant tests to confirm that JSDoc tag line numbers and descriptions are now correctly reported.

=== TASK kubernetes-incubator__client-python-50 | repo kubernetes-incubator/client-python | python
--- Issue
Support kube-config change context
Right now the load kube config function loads the default context. We should support loading other contexts and [maybe?] listing them.
--- Real fix (for your judgement only)
diff --git a/examples/example4.py b/examples/example4.py
new file mode 100644
index 000000000..af309b191
--- /dev/null
+++ b/examples/example4.py
@@ -0,0 +1,62 @@
+# Copyright 2016 The Kubernetes Authors.
+#
+# Licensed under the Apache License, Version 2.0 (the "License");
+# you may not use this file except in compliance with the License.
+# You may obtain a copy of the License at
+#
+#     http://www.apache.org/licenses/LICENSE-2.0
+#
+# Unless required by applicable law or agreed to in writing, software
+# distributed under the License is distributed on an "AS IS" BASIS,
+# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
+# See the License for the specific language governing permissions and
+# limitations under the License.
+
+import os
+
+from kubernetes import client, config
+from kubernetes.client import configuration
+
+
+def main():
+    config_file = os.path.join(os.path.expanduser('~'), '.kube', 'config')
+    contexts, active_context = config.list_kube_config_contexts(config_file)
+    if not contexts:
+        print("Cannot find any context in kube-config file.")
+        return
+    contexts = [context['name'] for context in contexts]
+    active_context = active_context['name']
+    for i, context in enumerate(contexts):
+        format_str = "%d. %s"
+        if context == active_context:
+            format_str = "* " + format_str
+        print(format_str % (i, context))
+    context = input("Enter context number: ")
+    context = int(context)
+    if context not in range(len(contexts)):
+        print(
+            "Number out of range. Using default context %s." %
+            active_context)
+        context_name = active_context
+    else:
+        context_name = contexts[context]
+
+    # Configs can be set in Configuration class directly or using helper
+    # utility
+    config.load_kube_config(config_file, context_name)
+
+    print("Active host is %s" % configuration.host)
+
+    v1 = client.CoreV1Api()
+    prin
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Correctly load Kubernetes config using GKE auth-provider
      `stat: can't specify None for path argument` when loading config from GKE cluster
The client appears to be unable to parse authentication information from GKE clusters.

The exception is being raised here: https://github.com/kubernetes-incubator/client-python/blob/master/kubernetes/config/kube_con
      Way: Handle optional config certificate files and format bearer tokens
      - Inspect kubeconfig parsing in the config loader where user certificate paths and cluster authentication tokens are read.
      - Check the helper method that resolves file paths or data for certificates, noting where `os.path.isfile` is called on potential `None` values when certificates are absent (such as in token-based or auth-provider authentication).
      - Modify the file resolution helper to only run `os.path.isfile` if the file path is non-empty/non-None.
      - Prefix retrieved auth-provider credentials and user tokens with 'Bearer ' when setting the authorization token on the config loader.
      - Run the kubeconfig test suite to verify token loading and certificate handling.
  [2] Goal: Ensure service account tokens are read without base64 encoding
      Contents of service account token mounted in POD is being base64 encoded
A project I have is using the client-python and using the mounted tokens wasn't working. After some investigation I figured out that the token file contents is being encoded in base64. From my understanding this content should 
      Way: Configure KubeConfigLoader to read raw file content
      - Identify the code responsible for loading user tokens from files, specifically the `_load_user_token` method within `KubeConfigLoader` and its use of the `FileOrData` utility class.
      - Understand that `FileOrData` incorrectly assumes file contents are always base64 encoded when converting to data, or that data provided is base64 encoded when converting to a file. This leads to double encoding/decoding for raw file contents like service account tokens.
      - Modify the `FileOrData` class to introduce a `base64_file_content` parameter in its constructor, defaulting to `True`. This parameter controls whether base64 encoding/decoding is applied when converting between file content and data representations.
      - Update the `FileOrData.as_file` method to conditionally apply `base64.decodestring` based on the `_base64_file_content` flag when creating a temporary file from provided data.
      - Update the `FileOrData.as_data` method to conditionally apply `base64.encodestring` based on the `_base64_file_content` flag when reading content from a file.
      - When `KubeConfigLoader._load_user_token` instantiates `FileOrData` for a token file, pass `base64_file_content=False` to ensure the token's raw content is read directly without base64 encoding.

=== TASK electron__electron-packager-1449 | repo electron/electron-packager | js
--- Issue
-all should not attempt to build Linux/ia32 since it fails to find required files
### Preflight Checklist
<!-- Please ensure you've completed the following steps by replacing [ ] with [x]-->

* [ X] I have read the [contribution documentation](https://github.com/electron/electron-packager/blob/main/CONTRIBUTING.md) for this project.
* [X ] I agree to follow the [code of conduct](https://github.com/electron/electron/blob/main/CODE_OF_CONDUCT.md) that this project follows, as appropriate.
* X[ ] I have searched the issue tracker for a bug that matches the one I want to file, without success.

### Issue Details

* **Electron Packager Version:**
Electron Packager 17.1.1
Node v18.9.0
Host Operating system: win32 10.0.22621 (x64)
* **Electron Version:**
v21.3.0
* **Operating System:**
Host Operating system: win32 10.0.22621 (x64)* 
**Last Known Working Electron Packager version:**:
  * <!-- (if applicable) e.g. 11.0.0 -->

### Expected Behavior
Since Linux/ia32 is deprecated for the version of Electron electron-package should not attempt to build it when --all is specified

### Actual Behavior
electron-packager fails with 404 error

C:\Development\YADAMU>npx electron-packager ./src yadamu --all  --out=c:\dist --overwrite
Official Linux/ia32 support is deprecated.
For more info: https://electronjs.org/blog/linux-32bit-support
Packaging app for platform darwin universal using electron v21.3.0 - Building x64 and arm64 slices now
Packaging app for platform
--- Real fix (for your judgement only)
diff --git a/src/targets.js b/src/targets.js
index f896aa4..aba6cc0 100644
--- a/src/targets.js
+++ b/src/targets.js
@@ -20,6 +20,7 @@ const buildVersions = {
   },
   linux: {
     arm64: '>= 1.8.0',
+    ia32: '<19.0.0-beta.1',
     mips64el: '^1.8.2-beta.5'
   },
   mas: {

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow notarization authorization using keychain and keychainProfile arguments
      validateAuthorizationArgs doesn't work when using `keychain` and `keychainProfile`
The `validateAuthorizationArgs` check is now really the `validateLegacyAuthorizationArgs` check from the `electron-notarize` package.
https://github.com/electron/electron-packager/blob/bde6031985cc83d25ffd78532e1ce3b
      Way: support notarytool keychain credentials by deferring notarization validation
      - Identify where notarization options are validated and passed downstream to electron-notarize.
      - Upgrade electron-notarize to a version that supports notarytool and keychain profiles (such as ^1.1.1).
      - Remove the eager call to validateAuthorizationArgs in the notarize options builder so validation is delegated to electron-notarize.
      - Condition the warning/overwrite of appBundleId so that it is only applied when tool is not 'notarytool'.
      - Update TypeScript definitions and ESLint configurations to support both legacy and notarytool notarization credential shapes.
      - Run the notarize test suite to confirm notarize configuration handling works properly.

=== TASK helidon-io__helidon-7988 | repo helidon-io/helidon | java
--- Issue
Upgrade to mysql-connector-j 8.2.0 or newer
Upgrade to mysql-connector-j 8.2.0 or newer. Need to do this in:

- [x] 2.x
- [x] 3.x
- [x] 4.x #7988


--- Real fix (for your judgement only)
diff --git a/archetypes/helidon/src/main/archetype/mp/custom/database-outputs.xml b/archetypes/helidon/src/main/archetype/mp/custom/database-outputs.xml
index 9c9d3e09e2..f89aabfe3d 100644
--- a/archetypes/helidon/src/main/archetype/mp/custom/database-outputs.xml
+++ b/archetypes/helidon/src/main/archetype/mp/custom/database-outputs.xml
@@ -306,8 +306,8 @@ docker run --rm --name mysql -p 3306:3306 \
                     </list>
                     <list key="dependencies">
                         <map>
-                            <value key="groupId">mysql</value>
-                            <value key="artifactId">mysql-connector-java</value>
+                            <value key="groupId">com.mysql</value>
+                            <value key="artifactId">mysql-connector-j</value>
                             <value key="scope">runtime</value>
                         </map>
                     </list>
diff --git a/archetypes/helidon/src/main/archetype/se/custom/database-output.xml b/archetypes/helidon/src/main/archetype/se/custom/database-output.xml
index 7d0b7622a4..5e4135c609 100644
--- a/archetypes/helidon/src/main/archetype/se/custom/database-output.xml
+++ b/archetypes/helidon/src/main/archetype/se/custom/database-output.xml
@@ -61,8 +61,8 @@ For details, see https://www.h2database.com/html/cheatSheet.html
                             <value key="artifactId">helidon-integrations-db-mysql</value>
                         </map>
                         <map>
-                            <value key="groupId">mysql</value>
-                            <value key="artifactId">mysql-connector-java</value>
+                            <value key="groupId">com.mysql</value>
+                            <value key="artifactId">mysql-connector-j</value>
                         </map>
                     </list>
                     <value key="db-health-check-stmt" if="${health}">SELECT 0</value>
diff --git a/common/socket/src/main/java/io/helidon/common/
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow excluding annotated metrics via scoping configuration filters
      Unable to disable application scope metrics: "allGets" and "personalisedGets"

## Environment Details
* Helidon Version: 4.0.10
* Helidon SE or Helidon MP: Helidon MP
* JDK version: 22.0.1
* OS: Darwin
* Docker version (if applicable):

----------

## Problem Description
In a Helidon MP Q
      Way: preserve metadata when wrapping disabled or delegated metrics in MP registry
      - Identify the cause of metadata mismatch exceptions on startup when metrics are filtered out via scoping configuration.
      - Add static helper method in Registry to extract Metadata (name, unit, description) directly from an underlying Meter instance.
      - Add resolvedScope helper in MetricImpl using SystemTagsManager to derive scope from a Meter delegate.
      - Add factory create methods accepting delegate Meters across HelidonCounter, HelidonGauge, HelidonHistogram, and HelidonTimer using the resolved scope and extracted metadata.
      - Update Registry meter wrapping lookup logic to instantiate wrapper metric implementations using the delegate's actual metadata and scope instead of empty/default metadata.
      - Run the tracing provider test suite and MP metrics tests to verify metadata consistency and test execution.
  [2] Goal: Return empty optional from current span method when no span is active
      [4.x] `Span.current()` does not return `Optional.empty()` if there is no current span, contrary to the documented contract (OTel)
Backport of #8573 for Helidon 4.x
      Way: check OpenTelemetry context for null span instead of calling current span
      - Locate the OpenTelemetry tracer provider implementation that resolves the currently active span.
      - Identify how the active span is retrieved; note whether calling Span.current() yields a fallback invalid/no-op span instance instead of null when no span is active in the context.
      - Retrieve the current OpenTelemetry context and call Span.fromContextOrNull(context) rather than Span.current(); return Optional.empty() if the returned span is null.
      - Run the tracing test suite to verify that empty Optional is returned when no span is active.

=== TASK sasstools__sass-lint-78 | repo sasstools/sass-lint | js
--- Issue
New Rule: Hex Notation
Check hexadecimal values are either all lowercase (default) or all uppercase.

as per [SCSS Lint](https://github.com/brigade/scss-lint/blob/master/lib/scss_lint/linter/README.md#hexnotation)
--- Real fix (for your judgement only)
diff --git a/docs/rules/hex-notation.md b/docs/rules/hex-notation.md
new file mode 100644
index 0000000..a8e9865
--- /dev/null
+++ b/docs/rules/hex-notation.md
@@ -0,0 +1,45 @@
+# Hex Notation
+
+Rule `hex-notation` will enforce the case of hexadecimal values
+
+## Options
+
+* `style`: `lowercase`/`uppercase` (defaults to `lowercase`)
+
+## Examples
+
+When `style: lowercase`, the following are allowed. When `style: uppercase`, the following are disallowed:
+
+```scss
+$foo-color: #fff;
+
+.bar {
+  background: linear-gradient(top, #cc2, #44d);
+}
+
+.baz {
+  color: #12a;
+}
+```
+
+When `style: uppercase`, the following are allowed. When `style: lowercase`, the following are disallowed:
+
+```scss
+$foo-color: #FFF;
+
+.bar {
+  background: linear-gradient(top, #CC2, #44D);
+}
+
+.baz {
+  color: #12A;
+}
+```
+
+In both cases the following will be allowed as the values contain only numbers:
+
+```scss
+.qux {
+  color: #123;
+}
+```
diff --git a/lib/config/sass-lint.yml b/lib/config/sass-lint.yml
index d343532..4c3bc41 100644
--- a/lib/config/sass-lint.yml
+++ b/lib/config/sass-lint.yml
@@ -27,6 +27,7 @@ rules:
   # Style Guide
   clean-import-paths: 1
   hex-length: 1
+  hex-notation: 1
   hex-validation: 1
   indentation: 1
   leading-zero: 1
diff --git a/lib/helpers.js b/lib/helpers.js
index 922b6d0..4310303 100644
--- a/lib/helpers.js
+++ b/lib/helpers.js
@@ -74,6 +74,55 @@ helpers.sortDetects = function (a, b) {
   return 0;
 };
 
+helpers.isNumber = function (val) {
+  if (isNaN(parseInt(val, 10))) {
+    return false;
+  }
+  return true;
+};
+
+helpers.isUpperCase = function (str) {
+  var pieces = str.split(''),
+      i,
+      result = 0;
+
+  for (i = 0; i < pieces.length; i++) {
+    if (!helpers.isNumber(pieces[i])) {
+      if (pieces[i] === pieces[i].toUpperCase() && pieces[i] !== pieces[i].toLowerCase()) {
+        result++;
+      }
+      else {
+        return false;
+      }
+    }
+  }
+  if (result) {
+    return true;
+  }
+  return false
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent sass-lint space-after-comma rule from crashing
      Space after Comma syntax error…
**Calling module within JS file**
```
/sass-lib/node_modules/sass-lint/lib/rules/space-after-comma.js:18
        if (next.is('space')) {
                ^
TypeError: Cannot read property 'is' of undefined
    at /sass-lib/node_modules/sass-lint/lib/rules/space-a
      Way: Prevent SASS linting crash on missing token
      - Identify the `TypeError: Cannot read property 'is' of undefined` occurring when the `space-after-comma` rule attempts to access properties of a non-existent 'next' token, specifically when a comma is the last element in a sequence.
      - Add a null/undefined check for the `next` token before attempting to access its properties (`next.is('space')`). This prevents the crash when a comma is the last token.
      - Refine the rule's logic to correctly handle cases where a comma is followed by a newline. Import the `os` module to detect operating system-specific end-of-line characters and ensure that a newline after a comma is not flagged as a missing space.
      - Expand the token types traversed by the rule from only 'operator' to include 'delimiter' to ensure comprehensive checking for commas in various contexts.
      - Run the provided tests to confirm the fix resolves the crash and the rule behaves as expected.
  [2] Goal: Fix linter false positive for -moz-osx-font-smoothing property
      no-misspelled-properties is incorrectly failing on '-moz-osx-font-smoothing'
I got around this issue by providing this exception to my config, but this would seem to be an error to me since font-awesome and many other icon fonts define this property for firefox.
```
  no-misspelled-properties:
  
      Way: Correctly strip multi-part vendor prefixes from CSS properties
      - Locate the `stripPrefix` helper function responsible for removing vendor prefixes from CSS property names.
      - Modify the `stripPrefix` function to specifically detect and remove the `-osx-` part of a vendor prefix when it follows another vendor prefix (e.g., `-moz-osx-`). This involves checking for 'osx' after the initial prefix and adjusting the string slicing accordingly.
      - Locate the `no-misspelled-properties` rule where property names are checked after prefix stripping.
      - Add a check to ensure the property name is not empty after prefix stripping before attempting to validate it against known properties. This prevents false positives if a complex prefix results in an empty string.
      - Run the relevant tests to confirm that the `stripPrefix` helper now correctly handles the `-moz-osx-font-smoothing` property and that the linter no longer flags it as misspelled.

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
--- Real fix (for your judgement only)
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
+      
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Fix covalent restart to preserve cluster configuration
      covalebt start + covalent restart starts covalent with no cluster 
      Way: Preserve cluster configuration on service restart
      - Identify that the `covalent restart` command fails to correctly re-apply the `no_cluster` setting, leading to an incorrect cluster state after restart.
      - Locate the `restart` function within `covalent_dispatcher/_cli/service.py` as the source of the incorrect behavior.
      - In the `restart` function, correct the `no_cluster` configuration lookup: change `get_config("user_interface.port")` to `get_config("sdk.no_cluster")` and map its string value to a boolean. Also, correct the `workers` lookup from `set_config("dask.num_workers")` to `get_config("dask.num_workers")`.
      - Verify that the `covalent restart` command now correctly preserves the `no_cluster` setting.
  [2] Goal: Enable power operator for Electron class instances
      Overloading the `__pow__` method in Electron class
Something as simple as the following would fail to dispatch right now because Electron class doesn't have the `__pow__` method defined similar to `__add__`, `__mul__`, etc.. Adding that would solve the issue.

```python
@ct.electron
def g(x):
 
      Way: Enable power operator for custom class instances
      - Identify that the custom class (Electron) does not support the power operator (**) because its `__pow__` special method is not defined.
      - Locate the class definition (Electron) and its existing binary operator implementations (e.g., `__add__`, `__mul__`) to understand the pattern for operator overloading.
      - Add the `__pow__` special method to the `Electron` class, implementing it to call the class's internal operator handling function (`get_op_function`) with the left operand, right operand, and the operator string "**".
      - Register the power operator string "**" to its corresponding function `operator.pow` within the class's internal mapping of binary operators (`_binary_ops`).
      - Run the specific test case designed to verify the `__pow__` method's functionality.

=== TASK core-gatech-group__serpent-tools-34 | repo CORE-GATECH-GROUP/serpent-tools | python
--- Issue
Add more message types
Add some future/depreciated warnings that can either piggy back off the [python builtin warnings](https://docs.python.org/2/library/warnings.html) but also conform to our messaging methodology
--- Real fix (for your judgement only)
diff --git a/serpentTools/__init__.py b/serpentTools/__init__.py
index 87c984c..bf0e5d3 100644
--- a/serpentTools/__init__.py
+++ b/serpentTools/__init__.py
@@ -5,13 +5,14 @@ ROOT_DIR = os.path.dirname(__file__)
 from serpentTools import settings
 
 # List TODOS/feature requests here for now
-# Compatability
+# Compatibility
 # TODO: Python 2 support
 # TODO: Test compatibility with earlier numpy releases
 # Usage/scripting
 # TODO: Update rc with dictionary
 # TODO: Update rc with yaml file into dictionary
 # TODO: Capture materials with underscores for depletion
+# TODO: Find a way to capture some or all of log messages for testing
 
 from ._version import get_versions
 __version__ = get_versions()['version']
diff --git a/serpentTools/objects/materials.py b/serpentTools/objects/materials.py
index 43e3db8..49e2ff4 100644
--- a/serpentTools/objects/materials.py
+++ b/serpentTools/objects/materials.py
@@ -3,7 +3,7 @@
 import numpy
 from matplotlib import pyplot
 
-
+from serpentTools.settings import messages
 from serpentTools.objects import _NamedObject
 
 
@@ -62,6 +62,7 @@ class DepletedMaterial(_NamedObject):
             List of strings corresponding to the raw data from the file
         """
         newName = self._convertVariableName(variable)
+        messages.debug('Adding {} data to {}'.format(newName, self.name))
         if isinstance(rawData, str):
             scratch = [float(item) for item in rawData.split()]
         else:
diff --git a/serpentTools/parsers/depletion.py b/serpentTools/parsers/depletion.py
index 2f31b49..c053805 100644
--- a/serpentTools/parsers/depletion.py
+++ b/serpentTools/parsers/depletion.py
@@ -37,22 +37,15 @@ class DepletionReader(MaterialReader):
     def __init__(self, filePath):
         MaterialReader.__init__(self, filePath, 'depletion')
         self._matPatterns = self._makeMaterialRegexs()
-        self.matchMatNVar = r'[A-Z]{3}_([0-9a-zA-Z]*)_([A-Z]*_?[A-Z]*)'
-        """
-        Captures materia
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Implement a configurable logging and custom exception framework
      Feature: Implement a messaging and exception framework
Implement a overarching data logger that controls warnings, errors, and debug statements that allows the user to set the verbosity through the `rc` system. Maybe piggyback off of the [logging module](https://docs.python.org/3.6/library/logging.h
      Way: implement logging and custom exceptions wired through settings dictionary
      - Create a logging submodule configured with a dictionary config (dictConfig), defining a base exception class, standard log wrapper functions (debug, info, warning, error), an allowed log levels list, and an updateLevel function that modifies the logger's level.
      - Update setting definitions and loaders to support an optional 'updater' callback on DefaultSetting and DefaultSettingsLoader. Add a 'verbosity' setting configured with allowed log levels and the updateLevel callback.
      - In the user settings loader's assignment method, invoke the setting's updater function (if defined) on the validated value before setting it, and log updates at debug level.
      - Insert logging calls (e.g. package version info, reader progress, warnings) across the package where appropriate.
      - Run test suite to verify settings and reader functionality continue to pass with the new settings and logging integrated.
  [2] Goal: Add a mechanism to capture and assert log messages during testing
      Determine and implement a way to test our message/logging systems
Working on the comparison feature, I realize we don't have a way to ensure that our logging system is actually logging what we want. This is more relevant for the testing the comparison utilities, and making sure that we are correctly
      Way: capture and assert log messages using dict handler
      - Inspect logger configuration and message functions to understand how application log messages are currently processed and routed.
      - Change the logging configuration to configure a named application logger with propagate=False rather than configuring the root logger directly, preventing test pollution.
      - Implement a custom logging.Handler (such as DictHandler) that overrides emit to store record.getMessage() in a dictionary keyed by record.levelname, and expose logger addHandler/removeHandler helper functions.
      - Create test utility classes/mixins that attach DictHandler during setUp, detach and close it during tearDown, and provide helper assertions (like assertMsgInLogs and assertMsgNotInLogs).
      - Update documentation and existing tests to verify log capture and assertion behavior.
  [3] Goal: Add comparison methods with tolerances for output readers and data containers
      [ENH] Ability to compare readers and objects
Implement a simple public method for readers and containers `.compare(other)` that would return `True` or `False` depending on the degree of difference between the two objects. This would be beneficial for comparing outputs across SERPENT versions, or sen
      Way: implement base object comparison methods with tolerances and confidence intervals
      - Inspect readers and data containers to identify data structures needing comparison (dictionaries of arrays, metadata, uncertainties, and grids), and identify logging mechanisms in messaging.
      - Create comparison logging helpers in the messaging module to log identical quantities, acceptable differences within lower/upper tolerances, and confidence interval bounds for uncertainties using debug, info, warning, and error log levels.
      - Create comparison utility functions in a new or extended utils module to handle relative differences (`directCompare`), confidence interval overlap calculations (`getOverlaps`, `getLogOverlaps`), and dictionary comparison routines (`compareDictOfArrays`, `getCommonKeys`).
      - Introduce a `BaseObject` class providing the public `.compare(other, lower=..., upper=..., sigma=...)` API that validates input types and tolerances before delegating to a private `_compare(self, other, lower, upper, sigma)` method.
      - Subclass `BaseObject` and implement `_compare` on target readers and containers (`ResultsReader`, `HomogUniv`, `DetectorReader`, `Detector`, `DepletionReader`, `DepletedMaterial`), continuing checks across all fields rather than returning early on individual failures.
      - Run comparison test suites to verify identical, tweaked, out-of-tolerance, and mismatched-shape cases pass across all readers and objects.

=== TASK import-js__eslint-plugin-import-2334 | repo import-js/eslint-plugin-import | js
--- Issue
Aliasing with `@` is not recognized as 'internal'
This seems to be a repeat of an issue that has been resolved and regressed twice now, see https://github.com/import-js/eslint-plugin-import/issues/2145 and https://github.com/import-js/eslint-plugin-import/issues/2249.

In my case the source code is private, but we're using a config that looks like:

```jsonc
// ...
"import/resolver": {
  "alias": [
    ["@", "./src"]
  ]
}
// ...
```

(See also https://github.com/johvin/eslint-import-resolver-alias.) And with source code like:

```jsx
// ...
import exampleInternalViaAlias from '@/exampleInternalViaAlias';

import exampleSibling from './exampleSibling';
// ...
```

When upgrading to v2.25.3 from v2.22.1, without any other changes to source code or other dependencies, we have now started getting false negatives like:

```
  3:1  error  `@/exampleInternalViaAlias` import should occur after import of `./exampleSibling`  import/order
```

Injecting some logs into the installed version of `eslint-plugin-import` in our node_modules, I can see that the import type is getting calculated as `'unknown'` (instead of `'internal'`) for `'@/exampleInternalViaAlias'`.
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index bac6b004..88d7c411 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -9,6 +9,7 @@ This change log adheres to standards from [Keep a CHANGELOG](https://keepachange
 ### Fixed
 - `importType`: avoid crashing on a non-string' ([#2305], thanks [@ljharb])
 - [`first`]: prevent crash when parsing angular templates ([#2210], thanks [@ljharb])
+- `importType`: properly resolve `@/*`-aliased imports as internal ([#2334], thanks [@ombene])
 
 ### Changed
 - [`no-default-import`]: report on the token "default" instead of the entire node ([#2299], thanks [@pmcelhaney])
@@ -950,6 +951,7 @@ for info on changes for earlier releases.
 
 [`memo-parser`]: ./memo-parser/README.md
 
+[#2334]: https://github.com/import-js/eslint-plugin-import/pull/2334
 [#2305]: https://github.com/import-js/eslint-plugin-import/pull/2305
 [#2299]: https://github.com/import-js/eslint-plugin-import/pull/2299
 [#2297]: https://github.com/import-js/eslint-plugin-import/pull/2297
@@ -1571,6 +1573,7 @@ for info on changes for earlier releases.
 [@noelebrun]: https://github.com/noelebrun
 [@ntdb]: https://github.com/ntdb
 [@nwalters512]: https://github.com/nwalters512
+[@ombene]: https://github.com/ombene
 [@ota-meshi]: https://github.com/ota-meshi
 [@panrafal]: https://github.com/panrafal
 [@paztis]: https://github.com/paztis
diff --git a/src/core/importType.js b/src/core/importType.js
index 3e579b94..ebdb306b 100644
--- a/src/core/importType.js
+++ b/src/core/importType.js
@@ -13,6 +13,11 @@ function baseModule(name) {
   return pkg;
 }
 
+function isInternalRegexMatch(name, settings) {
+  const internalScope = (settings && settings['import/internal-regex']);
+  return internalScope && new RegExp(internalScope).test(name);
+}
+
 export function isAbsolute(name) {
   return typeof name === 'string' && nodeIsAbsolute(name);
 }
@@ -25,33 +30,18 @@ export function isBuiltIn(name, settings, path) {
   return isCoreModule(base) || extras.indexOf(base) > -1;
 }
 
-export
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Enable module allowlist for import/no-namespace ESLint rule
      [no-namespace] allow namespace imports for specific modules
I want to disallow using namespaces as a rule, but some third-party libraries don't use default exports. The specific one that we use quite a bit in our application is [yup](https://github.com/jquense/yup). Even if we use TypeScript's `allo
      Way: add ignore option to ESLint import no-namespace rule
      - Examine the schema and AST visitor of the no-namespace rule implementation.
      - Update the rule schema to accept an options object with an 'ignore' property containing an array of unique strings.
      - In the rule's create function, read context.options[0].ignore and check the import's module specifier (node.parent.source.value) against the globs using minimatch with { matchBase: true } before reporting an error.
      - Document the ignore option and its glob behavior in the rule documentation.
      - Run the ESLint rule test suite including tests with ignored module patterns.

=== TASK lunarphp__lunar-2124 | repo lunarphp/lunar | php
--- Issue
Add support for Laravel 12
<!-- DO NOT THROW THIS AWAY -->
<!-- Fill out the FULL versions with patch versions -->

- Lunar version: latest
- Laravel Version: 12.x
- PHP Version: 8.4.0

### Expected Behaviour:
Allow to be installed in a laravel 12 project.

### Actual Behaviour:
  Problem 2
    - Root composer.json requires lunarphp/core * -> satisfiable by lunarphp/core[dev-main].
    - lunarphp/core dev-main requires laravel/framework ^10.0|^11.0 -> found laravel/framework[v10.0.0, ..., 10.x-dev, v11.0.0, ..., 11.x-dev] but it conflicts with your root composer.json require (^12.0).

### Steps To Reproduce:
Fresh laravel 12 project and try to install this package.
--- Real fix (for your judgement only)
diff --git a/.github/workflows/static-analysis.yml b/.github/workflows/static-analysis.yml
index 3ccfd745..f46dcd8b 100644
--- a/.github/workflows/static-analysis.yml
+++ b/.github/workflows/static-analysis.yml
@@ -12,10 +12,18 @@ jobs:
     strategy:
       fail-fast: false
       matrix:
-        php: ["8.2", "8.3"]
-        laravel: ["^10.0", "^11.0"]
+        php: ["8.2", "8.3", "8.4"]
+        laravel: ["^10.0", "^11.0", "^12.0"]
         dependencies: ["highest"]
-
+        exclude:
+          - php: "8.3"
+            laravel: "^10.0"
+          - php: "8.4"
+            laravel: "^10.0"
+          - php: "8.2"
+            laravel: "^11.0"
+          - php: "8.2"
+            laravel: "^12.0"
     name: "PHP ${{ matrix.php }} - L${{ matrix.laravel }} ${{ matrix.dependencies == 'highest' && '↑' || '↓' }}"
 
     runs-on: "ubuntu-latest"
diff --git a/composer.json b/composer.json
index 526ffbbf..05c4790b 100644
--- a/composer.json
+++ b/composer.json
@@ -1,5 +1,5 @@
 {
-    "name": "lunarphp/lunar",
+    "name": "lunarphp/lunarmono",
     "description": "Lunar Monorepo",
     "license": "MIT",
     "authors": [
@@ -10,44 +10,44 @@
     ],
     "require": {
         "awcodes/filament-badgeable-column": "^2.3.2",
-        "awcodes/shout": "^2.0.2",
-        "barryvdh/laravel-dompdf": "^2.0",
-        "cartalyst/converter": "^8.0|^9.0",
+        "awcodes/shout": "^2.0.4",
+        "barryvdh/laravel-dompdf": "^3.0",
+        "cartalyst/converter": "^8.0|^9.0|^10",
         "doctrine/dbal": "^3.6",
-        "dompdf/dompdf": "^2.0.7",
+        "dompdf/dompdf": "^3.1",
         "ext-bcmath": "*",
         "ext-exif": "*",
         "ext-intl": "*",
-        "filament/filament": "^3.2.25",
-        "filament/spatie-laravel-media-library-plugin": "^3.2",
+        "filament/filament": "^3.3.4",
+        "filament/spatie-laravel-media-library-plugin": "^3.3.4",
         "guzzlehttp/guzzle": "^7.3",
         "http-interop/http-factory-guzzle": "^1.2",
-        "kalnoy/nest
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Correctly store decimal AmountOff percentage values
      AmountOff percentage is cast to an int, even though it could contain a decimal value
The admin panel allows you to enter a decimal value when creating an AmountOff Discount with a percentage discount. However, it gets cast to an int in the AmountOff class, which results in the percentage being round
      Way: Allow decimal percentage values in discount calculations
      - Locate the `applyPercentage` method within the `AmountOff` discount type implementation, as the issue describes incorrect handling of decimal percentages for this specific discount type.
      - Change the type hint for the `$value` parameter in the `applyPercentage` method from `int` to `float` to correctly accommodate decimal percentage values.
      - Verify that the change correctly handles decimal percentages and does not introduce regressions by running the provided tests.

=== TASK gleam-lang__gleam-4354 | repo gleam-lang/gleam | rust
--- Issue
Tweak generated code for "Generate JSON encoder" action
Currently, one thing I like about the "Generate dynamic decoder" action is the fact that if you change the type that is being decoded, the decoder function will contain a compile error. For example, let's say you have this type:
```gleam
type Person {
  Person(name: String, age: Int)
}
```
The generated decoder will look like this:
```gleam
fn person_decoder() -> decode.Decoder(Person) {
  use name <- decode.field("name", decode.string)
  use age <- decode.field("age", decode.int)
  decode.success(Person(name:, age:))
}
```
Now let's say you want to add a new field, `job: String`. That decoder function will now error because you are trying to construct a `Person` without providing all the fields. This signals to the programmer that they need to delete the decoder and rerun the code action.
However, this is what the generared JSON encoder looks like for the above type currently.
```gleam
fn encode_person(person: Person) -> json.Json {
  json.object([
    #("name", json.string(person.name)),
    #("age", json.int(person.age)),
  ])
}
```
Even with a new field, this compiles fine. Nothing here is ensuring all fields are covered, so it's possible that someone will forget to update the encoder, and end up introducing a bug in the code.
To fix this, I propose a slight change in the generated code. Instead, it would look like this:
```gleam
fn encode_person(person: Person) -> json.Json {
  let Person(name:, age:) = person
  jso
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index 828b3100f..b04b3bd25 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -49,6 +49,42 @@
 
   ([Surya Rose](https://github.com/GearsDatapacks))
 
+- The function generated by the "Generate JSON encoder" code action has been
+  slightly modified so that it will now fail to compile if the type has new
+  fields added, ensuring the programmer remembers to re-run the code action.
+  For example, for this type:
+
+  ```gleam
+  type Person {
+    Person(name: String, age: Int)
+  }
+  ```
+
+  The following code used to be generated:
+
+  ```gleam
+  fn encode_person(person: Person) -> json.Json {
+    json.object([
+      #("name", json.string(person.name)),
+      #("age", json.int(person.age)),
+    ])
+  }
+  ```
+
+  But now, this code is generated:
+
+  ```gleam
+  fn encode_person(person: Person) -> json.Json {
+    let Person(name:, age:) = person
+    json.object([
+      #("name", json.string(name)),
+      #("age", json.int(age)),
+    ])
+  }
+  ```
+
+  ([Surya Rose](https://github.com/GearsDatapacks))
+
 ### Formatter
 
 ### Bug fixes
@@ -68,7 +104,7 @@
 - Fixed a bug where using the "Convert to pipe" code action on a function or
   record capture produces invalid code.
   ([Matias Carlander](https://github.com/matiascr))
-  
+
 
 ## v1.9.1 - 2025-03-10
 
diff --git a/compiler-core/src/language_server/code_action.rs b/compiler-core/src/language_server/code_action.rs
index 4b34eadda..95f04e21c 100644
--- a/compiler-core/src/language_server/code_action.rs
+++ b/compiler-core/src/language_server/code_action.rs
@@ -3407,7 +3407,23 @@ impl<'a> GenerateJsonEncoder<'a> {
         // require pattern matching on the argument as we can access all its fields
         // with the usual record access syntax.
         if rest.is_empty() {
-            return self.constructor_encoder(mode, first, custom_type.name.clone(), record_name, 2);
+            let encoder = self.constructor_encoder(mode, first, custom_type.name.clone(), 2
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Fix Erlang code generation for block expressions in record updates
      Incorrect Erlang code with block pattern during record update
<!--
Please include the following:
- Steps and code to reproduce the problem
- Versions for Gleam, OS, the runtime and any other software used
- Logs from running Gleam with the `GLEAM_LOG` environment variable set to `trace`
-->
Hi
      Way: Wrap complex single-expression blocks in begin-end during Erlang codegen
      - Locate block expression generation in the Erlang code generator module.
      - Identify where single-statement blocks are simplified to parentheses `(...)` instead of `begin ... end`.
      - Update single-expression block handling in Erlang codegen to check `!needs_begin_end_wrapping(expression)` instead of only checking if it's a non-pipe statement, ensuring statements that emit variable assignments (like record updates) get wrapped in `begin ... end`.
      - Remove unused helper methods like `is_non_pipe_expression` from the typed statement AST.
      - Add regression test with nested record updates wrapped in block expressions and run compiler tests.
  [2] Goal: Make generate function action include argument labels
      "Generate function" code action doesn't take labels into account
At the moment if I have a function that doesn't exist and is also using labels like this: `wibble(wobble: 1)`, the "Generate function" code action will generate a function that has no labels and so will be invalid. It would be nice if 
      Way: include argument labels in generated functions from calls
      - Identify where undefined function calls trigger the 'Generate function' code action and inspect how function arguments are constructed.
      - Add a helper method `is_invalid()` to `TypedExpr` to detect invalid expression nodes.
      - Implement a helper to validate argument labels in call expressions, verifying that no duplicate labels exist and unlabelled arguments do not appear after labelled ones.
      - Update the AST visitor for `GenerateFunction` to intercept `visit_typed_expr_call` when the called expression is invalid and in range, extracting call argument labels.
      - Update code generation to include the label prefix in parameter definitions when present.
      - Run the language server code action test suite to verify generated function signature and label correctness.
  [3] Goal: Prevent convert to pipe code action on use expressions
      "Convert to pipe" code action interacts weirdly with `use`
The new "Convert to pipe" code action doesn't properly take into account `use` expressions. For example, given this code:
```gleam
import gleam/result

pub fn main() {
  use x <- result.try(todo)
  Ok(x + 1)
}
```

The Language Server offers
      Way: disable pipe transformation on use expression call targets
      - Locate the language server code action handler for converting expressions to pipe calls (e.g. ConvertToPipe AST visitor).
      - Identify how use expressions and function call targets within use expressions are visited during code action traversal.
      - Modify the AST visitor logic for ConvertToPipe to skip or ignore function calls that are on the right-hand side of a `use` expression so that pipe conversion actions are not offered for them.
      - Run language server test suite to ensure convert-to-pipe is suppressed on use call targets but still works inside use bodies.

=== TASK robintail__express-zod-api-930 | repo RobinTail/express-zod-api | ts
--- Issue
Parameter descriptions are only outputted on referenced schema, making them invisible in docs
We recently upgraded to express-zod-api v10. This, in general, massively cleaned up our OpenAPI schema and made it work much better with code generation tools (thanks for that! 🙏). Still, it seems like it also introduced a new bug or potentially a new gotcha we weren't aware of.

Here's how to reproduce the problem:

1. Create an endpoint or middleware that has an input with a description:

```ts
export const testEndpoint = endpointFactory.build({
  input: z.object({
    cursor: z
      .string()
      .optional()
      .describe(
        'An optional cursor string used for pagination.' +
          ' This can be retrieved from the `next` property of the previous page response.',
      ),
  }),
  output: z.object({}),
  handler: async () => {
    return {}
  },
})
```

2. Generate the OpenAPI schema and look at the output:

```yml
parameters:
  - name: cursor
    in: query
    required: false
    description: GET /hris/employees parameter
    schema:
      $ref: "#/components/schemas/GetHrisEmployeesParameterCursor"
```

```yml
GetHrisEmployeesParameterCursor:
  type: string
  description: An optional cursor string used for pagination. This can be
    retrieved from the `next` property of the previous page response.
```

The custom description is outputted, but only on the referenced schema, not the actual parameter. On the parameter, the des
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index c4d37e46..64bcd9b3 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -2,6 +2,11 @@
 
 ## Version 10
 
+### v10.1.3
+
+- Fixed issue #929, found and reported by [@shroudedcode](https://github.com/shroudedcode).
+  - Customized description of request parameters have not been depicted correctly when generating the documentation.
+
 ### v10.1.2
 
 - Fixed issue #907, found and reported by [@McMerph](https://github.com/McMerph).
diff --git a/example/example.swagger.yaml b/example/example.swagger.yaml
index 068b5fe8..77484517 100644
--- a/example/example.swagger.yaml
+++ b/example/example.swagger.yaml
@@ -84,7 +84,7 @@ paths:
         - name: id
           in: query
           required: true
-          description: GET /v1/user/retrieve parameter
+          description: a numeric string containing the id of the user
           schema:
             type: string
             pattern: /\d+/
diff --git a/src/open-api-helpers.ts b/src/open-api-helpers.ts
index 7efa5641..a3aea324 100644
--- a/src/open-api-helpers.ts
+++ b/src/open-api-helpers.ts
@@ -642,7 +642,9 @@ export const depictRequestParams = ({
         name,
         in: isPathParam(name) ? "path" : "query",
         required: !shape[name].isOptional(),
-        description: `${method.toUpperCase()} ${path} ${clue}`,
+        description:
+          (oas30.isSchemaObject(depicted) && depicted.description) ||
+          `${method.toUpperCase()} ${path} ${clue}`,
         schema: result,
         ...depictParamExamples(schema, false, name),
       };

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support ZodDate schema types when generating OpenAPI documentation
      Issue: Generating documentation gives error:  "Zod type ZodDate is unsupported"
Whenever I try to generate OpenAPI spec using:

```
const yamlString = new OpenAPI({
  routing: routes, // the same routing and config that you use to start the server
  config,
  version: '1',
  title: 'Posterr A
      Way: Add custom error message for unsupported ZodDate in OpenAPI generator
      - Locate schema depiction rules and handlers for mapping Zod types to OpenAPI specifications.
      - Implement a specialized depict handler for `z.ZodDate` that throws an `OpenAPIError` explaining to use direction-specific date helpers (e.g. `z.dateIn()` or `z.dateOut()`) depending on the `isResponse` context.
      - Register the new `ZodDate` depiction handler into the schema depicting rules dictionary.
      - Run the test suite to verify the custom error message and ensure existing OpenAPI schema generation tests pass.
  [2] Goal: Enable dateIn inputs to work in middleware without type errors
      Bug: Cannot use `dateIn` as an input for a middleware
Another small bug my team discovered:

Right now, it's not possible to define a middleware with a `dateIn` input. Calling an endpoint using such a middleware always results in a `<field_name>: Expected string, received date` error.

I didn't 
      Way: Prevent double parsing of middleware input schemas
      - Locate the helper that extracts raw request data (getInitialInput) and the endpoint logic that runs middlewares.
      - Rename getInitialInput to getInput and update all imports to use the new name.
      - Make the input object immutable for the endpoint: change the constructor parameter type to Readonly<any> and stop mutating it with Object.assign.
      - In #runMiddlewares, replace the in‑place mutation of input with a call to def.input.parseAsync(input) and pass the parsed value to the middleware without altering the original object.
      - Adjust #parseAndRunHandler and the final handling code to accept the immutable input (named input) instead of the previously mutated initialInput.
      - Run the full test suite to ensure the previous failure (dateIn in middleware) is resolved and no new failures appear.

=== TASK laravel__framework-37487 | repo laravel/framework | php
--- Issue
[Eloquent] withCount()->having()->count() generates wrong query
<!-- DO NOT THROW THIS AWAY -->
<!-- Fill out the FULL versions with patch versions -->

- Laravel Version: 8.42.1
- PHP Version: 8.0.3
- Database Driver & Version: MariaDB

### Description:
Given two simple models
```php
Illuminate\Support\Facades\Schema::create('users', function (Blueprint $table) {
  $table->id();
});
Illuminate\Support\Facades\Schema::create('roles', function (Blueprint $table) {
  $table->id();
 $table->foreignId('user_id')
                ->constrained()
                ->cascadeOnDelete()->cascadeOnUpdate();
});
class Role extends Model {
    public function user() {
        return $this->belongsTo( User::class );
    }
}

class User extends Model {
     public function codes() {
        return $this->hasMany( Role::class );
    }
}
```

Running the following statement results in an sql error:

```php
User::query()->withCount('roles')->having('roles_count', '>', '2')->count()
```
```
Illuminate\Database\QueryException with message 'SQLSTATE[42S22]: Column not found: 1054 Unknown column 'roles_count' in 'having clause' (SQL: select count(*) as aggregate from `users` having `roles_count` > 2)'
```

But the following runs without error

```php
User::query()->withCount('roles')->having('roles_count', '>', '2')->get()
```

The count method should be interchangeable and work with any query that `get` works with but currently it doesn't seem to gener
--- Real fix (for your judgement only)
diff --git a/src/Illuminate/Database/Query/Builder.php b/src/Illuminate/Database/Query/Builder.php
index 9df7d46eec..0ef10ac1c2 100755
--- a/src/Illuminate/Database/Query/Builder.php
+++ b/src/Illuminate/Database/Query/Builder.php
@@ -2826,8 +2826,8 @@ public function average($column)
      */
     public function aggregate($function, $columns = ['*'])
     {
-        $results = $this->cloneWithout($this->unions ? [] : ['columns'])
-                        ->cloneWithoutBindings($this->unions ? [] : ['select'])
+        $results = $this->cloneWithout($this->unions || $this->havings ? [] : ['columns'])
+                        ->cloneWithoutBindings($this->unions || $this->havings ? [] : ['select'])
                         ->setAggregate($function, $columns)
                         ->get($columns);
 
diff --git a/src/Illuminate/Database/Query/Grammars/Grammar.php b/src/Illuminate/Database/Query/Grammars/Grammar.php
index b7305e8ea3..d7bc534da1 100755
--- a/src/Illuminate/Database/Query/Grammars/Grammar.php
+++ b/src/Illuminate/Database/Query/Grammars/Grammar.php
@@ -45,7 +45,7 @@ class Grammar extends BaseGrammar
      */
     public function compileSelect(Builder $query)
     {
-        if ($query->unions && $query->aggregate) {
+        if (($query->unions || $query->havings) && $query->aggregate) {
             return $this->compileUnionAggregate($query);
         }
 

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Fix db:show command for MySQL 5.7 enum types
      New command php artisan db:show returns exception on laravel version 9.24 with mysql 5.7 database
<!-- DO NOT THROW THIS AWAY -->
<!-- Fill out the FULL versions with patch versions -->

- Laravel Version: 9.24.0
- PHP Version: 8.1.9
- Database Driver & Version: Mysql 5.7.38

### Description:
      Way: Register doctrine type mappings for database inspection commands
      - Locate database inspection commands that query database schema metadata via Doctrine DBAL (such as `ShowCommand`, `TableCommand`, and their shared base class `DatabaseInspectionCommand`).
      - Define a mapping of unmapped database column types (e.g. `enum`, `bit`, `geometry`, `sysname` mapped to `string`) on the base inspection command class.
      - Add a helper method `registerTypeMapping` in the base command that iterates through the type mappings and calls `registerDoctrineTypeMapping($type, $value)` on the Doctrine `AbstractPlatform`.
      - Call `registerTypeMapping($schema->getDatabasePlatform())` in `ShowCommand::handle` and `TableCommand::handle` right after obtaining the schema manager.
      - Run the test suite to ensure inspection commands and unrelated components pass cleanly.
  [2] Goal: Fix signed URL validation for empty querystring parameters
      Signed URLs containing empty querystring values will not validate
- Laravel Version: 8.41.0
- PHP Version: 7.4.16
- Database Driver & Version: n/a

### Description:

If a url contains querystring parameters with empty values, the generated signed url will not validate. 

### Steps To Reprodu
      Way: Validate signed URLs with empty query parameters using raw string
      - Locate the `hasCorrectSignature` method within the `UrlGenerator` class, which is responsible for verifying the signature of a URL.
      - Understand that the current signature validation reconstructs the query string from parsed request parameters, which can lead to discrepancies when empty query string values are present in the original URL.
      - Modify the `hasCorrectSignature` method to use the raw `QUERY_STRING` from the request server parameters instead of reconstructing it from parsed request parameters. This ensures the exact query string used for signing is used for verification.
      - Ensure the `signature` parameter is removed from the raw query string before hashing, as it was in the previous implementation.
      - Run the provided tests to confirm the fix resolves the issue and does not introduce regressions.
  [3] Goal: Support implicit route model binding for bound interface types
      [5.2] Implicit route model binding a binded interface
Hello, I try to do a implicit route model binding on a interface that is binded to my User model but it wont work. I created a thread on Laracasts about this, so you can see the example over there. https://laracasts.com/discuss/channels/laravel/i
      Way: allow interface types in reflection subclass checks
      - Identify where parameter type reflection checks are performed for dependency injection or route model binding.
      - Check how `isParameterSubclassOf` validates whether the parameter's class/type exists before reflection.
      - Update the parameter type existence check to accept both `class_exists($paramClassName)` and `interface_exists($paramClassName)` before calling `(new ReflectionClass($paramClassName))->isSubclassOf($className)`.
      - Run the test suite to verify that existing reflection-dependent operations and unit tests pass.

=== TASK geopandas__geopandas-3591 | repo geopandas/geopandas | python
--- Issue
Slight change in GeoDataFrame constructor behavior related to geometry column detection
In v1.0.1 this worked:
```python
gdf = gpd.GeoDataFrame(
    data=[[np.nan, np.nan]],
    columns=["geometry", "something"],
    crs="EPSG:4326",
)
```
silently converting the single value for geometry from NaN to `None`.

In v1.1.0, it fails with:

`ValueError: Assigning CRS to a GeoDataFrame without a geometry column is not supported. Supply geometry using the 'geometry=' keyword argument, or by providing a DataFrame with column name 'geometry'`

And indeed, even though we already _have_ a column named 'geometry', it does work if you add `geometry="geometry"`, silently converting the NaN to `None`.

It also doesn't complain if NaN isn't the only value, for example:
```python
gdf = gpd.GeoDataFrame(
    {
        "geometry": [np.nan, Point(0, 0)],
        "something": [np.nan, np.nan],
    },
    crs="EPSG:4326",
)
```
(again the NaN becomes `None`)
--- Real fix (for your judgement only)
diff --git a/CHANGELOG.md b/CHANGELOG.md
index f3cc8855..56554d4f 100644
--- a/CHANGELOG.md
+++ b/CHANGELOG.md
@@ -1,5 +1,9 @@
 # Changelog
 
+## Version 1.1.1
+
+- Fix regression in the GeoDataFrame constructor when np.nan is given as an only geometry (#3591).
+
 ## Version 1.1.0 (June 1, 2025)
 
 Notes on dependencies:
diff --git a/geopandas/geodataframe.py b/geopandas/geodataframe.py
index 1a607cbb..243d9875 100644
--- a/geopandas/geodataframe.py
+++ b/geopandas/geodataframe.py
@@ -211,6 +211,8 @@ class GeoDataFrame(GeoPandasBase, DataFrame):
                 or geom_dtype == "object"
                 # special case for geometry = [], has float dtype
                 or (len(self) == 0 and geom_dtype == "float")
+                # special case for geometry = [np.nan]
+                or ((not self.empty) and self["geometry"].isna().all())
             ):
                 try:
                     self["geometry"] = _ensure_geometry(self["geometry"].values, crs)

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Propagate CRS to GeometryArray when setting a geometry column on GeoDataFrame
      BUG: CRS not propagated to array
I have found one issue caused by #1339. OSMnx [uses](https://github.com/gboeing/osmnx/blob/a4b4dc9efb8223b8aab257ff8825454d4bd06ddc/osmnx/projection.py#L71-L75) following pattern:

```py
geometry = Point(0,0)  # my addition

gdf = gpd.GeoDataFrame()
gdf.crs = 4
      Way: Broadcast scalar geometry on assignment to retain dataframe CRS
      - Identify the DataFrame __setitem__ override where setting the active geometry column converts incoming values using _ensure_geometry.
      - Reproduce missing CRS on GeometryArray when setting a scalar (e.g. None or a BaseGeometry instance) into the geometry column of a GeoDataFrame with crs already defined.
      - In the __setitem__ method, check if the assigned value to the geometry column is scalar using pd.api.types.is_scalar or isinstance(BaseGeometry), and broadcast it to a list matching dataframe length before passing to _ensure_geometry with self.crs.
      - Run the geometry array CRS test suite to verify scalar assignment retains the GeoDataFrame CRS.
  [2] Goal: Allow CRS transformation when geometry column contains missing or None values
      BUG: to_crs() fails if there's None in geometry
With shapely backend, `to_crs()` fails on `None` in geometry column.

```py
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

gpd.options.use_pygeos = False

df = pd.DataFrame(
    {'City': ['Buenos Aires', 'Bras
      Way: Bypassing transformation functions for missing geometry values
      - Identify the vectorization loop where geometric transformations are applied element-by-element across geometry arrays.
      - Reproduce the failure by calling the coordinate reference system transformation on an array containing None or missing geometry values.
      - Update the transformation loop to inspect each element with a missing-value check, preserving missing values as-is in the result array and only transforming valid geometries.
      - Run the CRS transformation test suite covering missing data to verify successful execution.
  [3] Goal: Preserve custom geometry column name when initializing GeoDataFrame from another GeoDataFrame
      API (BUG?): GeoDataFrame(gdf) constructor: preserve geometry column name?
When passing a GeoDataFrame object to the GeoDataFrame object, it currently does not preserve the geometry column name if not "geometry":

```
In [1]: df = geopandas.read_file(geopandas.datasets.get_path("naturalearth_lowre
      Way: Preserve custom geometry column name when initializing GeoDataFrame from another GeoDataFrame
      - Locate the GeoDataFrame constructor in the geodataframe module.
      - Modify the GeoDataFrame constructor logic: when the input data is already a GeoDataFrame and the geometry parameter is not explicitly provided, retrieve and preserve the geometry column name from the input GeoDataFrame instead of defaulting to 'geometry'.
      - Verify the fix by running the test suite, specifically checking that initializing a GeoDataFrame from another GeoDataFrame with a custom geometry column name preserves that custom name.

=== TASK sasstools__sass-lint-893 | repo sasstools/sass-lint | js
--- Issue
UTF-8 w/ BOM + @mixin causes fatal parsing error
Demo repo [here](https://github.com/mattdistefano/sass-lint-bom-issue-demo)

Short version is that when you have a file encoded as UTF-8 w/ BOM that starts with `@mixin`, the CLI linter fails with a `1:1  error  Please check validity of the block starting from line #1  Fatal`.

UTF-8 w/out BOM works fine, as does UTF-8 w/ BOM without `@mixin` at the start. 

Happens on OS X w/ node 6.2.0 and Windows 10 w/ node 5.8.0.

The gulp-sass-lint plugin actually works fine in all scenarios.
--- Real fix (for your judgement only)
diff --git a/lib/groot.js b/lib/groot.js
index 986ce8b..1d2ee52 100644
--- a/lib/groot.js
+++ b/lib/groot.js
@@ -3,14 +3,15 @@
 //////////////////////////////
 'use strict';
 
-var gonzales = require('gonzales-pe'),
-    fm = require('front-matter');
+var gonzales = require('gonzales-pe');
+var fm = require('front-matter');
+var helpers = require('./helpers');
 
 module.exports = function (text, syntax, filename) {
   var tree;
 
   // Run `.toString()` to allow Buffers to be passed in
-  text = text.toString();
+  text = helpers.stripBom(text.toString());
 
   // if we're skipping front matter do it here, fall back to just our text in case it fails
   if (fm.test(text)) {
diff --git a/lib/helpers.js b/lib/helpers.js
index d2cb330..0b9af3c 100644
--- a/lib/helpers.js
+++ b/lib/helpers.js
@@ -394,4 +394,24 @@ helpers.isPartialStringMatch = function (needle, haystack) {
   return false;
 };
 
+/**
+ *  A copy of the the stripBom module from https://github.com/sindresorhus/strip-bom/blob/master/index.js
+ *  The module requires node > 4 whereas we support earlier versions.
+ *  This function strips the BOM marker from the beginning of a file
+ *
+ * @param {string} str - The string we wish to strip the BOM marker from
+ * @returns {string} The string without a BOM marker
+ */
+helpers.stripBom = function (str) {
+  if (typeof str !== 'string') {
+    throw new TypeError('Expected a string, got ' + typeof str);
+  }
+
+  if (str.charCodeAt(0) === 0xFEFF) {
+    return str.slice(1);
+  }
+
+  return str;
+};
+
 module.exports = helpers;

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent sass-lint space-after-comma rule from crashing
      Space after Comma syntax error…
**Calling module within JS file**
```
/sass-lib/node_modules/sass-lint/lib/rules/space-after-comma.js:18
        if (next.is('space')) {
                ^
TypeError: Cannot read property 'is' of undefined
    at /sass-lib/node_modules/sass-lint/lib/rules/space-a
      Way: Prevent SASS linting crash on missing token
      - Identify the `TypeError: Cannot read property 'is' of undefined` occurring when the `space-after-comma` rule attempts to access properties of a non-existent 'next' token, specifically when a comma is the last element in a sequence.
      - Add a null/undefined check for the `next` token before attempting to access its properties (`next.is('space')`). This prevents the crash when a comma is the last token.
      - Refine the rule's logic to correctly handle cases where a comma is followed by a newline. Import the `os` module to detect operating system-specific end-of-line characters and ensure that a newline after a comma is not flagged as a missing space.
      - Expand the token types traversed by the rule from only 'operator' to include 'delimiter' to ensure comprehensive checking for commas in various contexts.
      - Run the provided tests to confirm the fix resolves the crash and the rule behaves as expected.
  [2] Goal: Fix linter false positive for -moz-osx-font-smoothing property
      no-misspelled-properties is incorrectly failing on '-moz-osx-font-smoothing'
I got around this issue by providing this exception to my config, but this would seem to be an error to me since font-awesome and many other icon fonts define this property for firefox.
```
  no-misspelled-properties:
  
      Way: Correctly strip multi-part vendor prefixes from CSS properties
      - Locate the `stripPrefix` helper function responsible for removing vendor prefixes from CSS property names.
      - Modify the `stripPrefix` function to specifically detect and remove the `-osx-` part of a vendor prefix when it follows another vendor prefix (e.g., `-moz-osx-`). This involves checking for 'osx' after the initial prefix and adjusting the string slicing accordingly.
      - Locate the `no-misspelled-properties` rule where property names are checked after prefix stripping.
      - Add a check to ensure the property name is not empty after prefix stripping before attempting to validate it against known properties. This prevents false positives if a complex prefix results in an empty string.
      - Run the relevant tests to confirm that the `stripPrefix` helper now correctly handles the `-moz-osx-font-smoothing` property and that the linter no longer flags it as misspelled.

=== TASK pajlads__dinkplugin-273 | repo pajlads/DinkPlugin | java
--- Issue
200M experience notification
Currently, our post-99 notifications are enabled with the "Notify on Virtual Levels" option - I feel like this option should also notify when the user reaches 200M experience

There are some things that would need to be tested in-game for this feature to make sure it doesn't continue to notify

1. When a user has 199,999,999 XP and receives 2 XP
2. When a user has 200,000,000 XP - what do further skill change events look like?
--- Real fix (for your judgement only)
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
     priva
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support XP milestone notifications instead of virtual levels for post-99 experience
      XP Milestones instead of Virtual Levels for Post 99 XP
### Checklist

- [X] I've checked the `Unreleased` section of the [changelog](https://github.com/pajlads/DinkPlugin/blob/master/CHANGELOG.md) for newly added features that sound like my suggestion.
- [X] I've searched the [issues and pull req
      Way: Add post-99 experience milestone notifications and total experience placeholder
      - Identify the configuration interface and level notification handling logic tracking skill levels and experience changes.
      - Add configuration items for the post-99 XP notification interval (e.g., in millions of XP) and update documentation/metadata for %TOTAL_XP% template replacement and new XP milestone notification types.
      - Add an XP_MILESTONE NotificationType and create a corresponding notification data class (XpNotificationData) capturing skill XP mapping, achieved milestone skills, and interval.
      - Update LevelNotifier to track skill XP thresholds across ticks/stat changes, checking if skill XP crosses intervals (or reaches 200M XP) for skills at or above level 99, and dispatching XP milestone notifications independently from virtual level settings.
      - Run the test suite to verify notification data generation, templating, and existing level notification functionality.
  [2] Goal: Post XP milestone when obtaining skilling pet
      Pet: No XP milestone when getting skilling pet
**Dink version:** 1.3.1

Just got my first skilling pet and yet the plugin didn't posted the milestone (at xxx XP). The milestone did get posted in the GIM chat.
![No milestone skilling pet](https://user-images.githubusercontent.com/11279045/22668202
      Way: Delay notification to await related data
      - Identify the notification logic that triggers prematurely, specifically where the primary event (pet acquisition) is detected but related information (XP milestone) is not yet available.
      - Introduce a mechanism to delay the notification. This involves adding a counter to track game ticks since the primary event was detected, and a maximum wait threshold.
      - Modify the notification trigger to only fire if the related data has arrived, or if the maximum wait threshold has been exceeded. This ensures the notification is sent eventually, even if the related data never arrives.
      - Ensure the delay mechanism is reset when the notification state is cleared, preventing stale delays from affecting subsequent events.
      - Run the provided tests to confirm the fix works and doesn't introduce regressions.
  [3] Goal: Ensure achievement, quest, and collection log data updates proactively
      Feature request: Make generic achievement metadata more accesible
I am currently working on making an OSRS 2011-style adventurer's log (see pic). It is using Dink as it's engine for pushing log data (admittedly not the intended usecase). I chose Dink because it is avaiable in both HDOS as well as Ru
      Way: Send comprehensive character metadata on login to custom webhook
      - Identify the need for proactive updates of character statistics (achievements, quests, collection log) that are not triggered by in-game events, especially for maxed accounts.
      - Introduce a new `MetaNotifier` component responsible for gathering various character statistics.
      - Add a new configuration option, `metadataWebhook`, to allow users to specify a custom webhook URL for these character metadata notifications. This webhook should be placed in an 'Advanced' section.
      - Implement logic within the `MetaNotifier` to collect character data (e.g., collection log progress, combat achievement points, quest points, skill levels) upon player login. This notification should be delayed by a few seconds to ensure all data is loaded.
      - Define a new `LOGIN` notification type in `NotificationType` to categorize these character metadata messages.
      - Ensure the webhook URL parsing logic can handle multiple URLs and gracefully filter out any null or invalid entries.

=== TASK mie-lab__trackintel-274 | repo mie-lab/trackintel | python
--- Issue
Add documentation to accessor methods.
The accessor functions with the pattern `as_triplegs.xyz` do not have a complete documentation, but rather refer to the wrapped functions in a different modules. This is inconvenient for working in an editor, as one cannot look up the functionality of the various parameters in the docstring. Instead one has to look up the referenced docstring seperatly.

Two ways to change that would be:
1. Just copy the docstring over at every change. 
2. Use a decorator to copy the docstring directly.

The first way is rather error-prone as one has to remember to change both docstrings at any change. Here it would be good to write a test that checks the equality of the `__doc__` attribute of the functions. The second way can be unclear in the documentation as it replaces silently the existing docstring. Also other problem arises in terms of circular imports etc. .
--- Real fix (for your judgement only)
diff --git a/docs/modules/model.rst b/docs/modules/model.rst
index 9638bff..0b779b9 100644
--- a/docs/modules/model.rst
+++ b/docs/modules/model.rst
@@ -57,24 +57,45 @@ Available Accessors
 
 The following accessors are available within *trackintel*.
 
+UsersAccessor
+-------------
+
 .. autoclass:: trackintel.model.users.UsersAccessor
 	:members:
 
+PositionfixesAccessor
+---------------------
+
 .. autoclass:: trackintel.model.positionfixes.PositionfixesAccessor
 	:members:
 
+StaypointsAccessor
+------------------
+
 .. autoclass:: trackintel.model.staypoints.StaypointsAccessor
 	:members:
 
+TriplegsAccessor
+----------------
+
 .. autoclass:: trackintel.model.triplegs.TriplegsAccessor
 	:members:
 
+LocationsAccessor
+-----------------
+
 .. autoclass:: trackintel.model.locations.LocationsAccessor
 	:members:
 
+TripsAccessor
+-------------
+
 .. autoclass:: trackintel.model.trips.TripsAccessor
 	:members:
 
+ToursAccessor
+-------------
+
 .. autoclass:: trackintel.model.tours.ToursAccessor
 	:members:
 
@@ -87,7 +108,7 @@ Data Model (SQL)
 
 For a general description of the data model, please refer to the 
 :doc:`/modules/model`. You can download the 
-complete SQL script `here <https://github.com/mie-lab/trackintel/blob/master/sql/create_tables_pg.sql>`_ 
+complete SQL script `here <https://github.com/mie-lab/trackintel/blob/master/sql/create_tables_pg.sql>`__
 in case you want to quickly set up a database. Also take a look at the `example on github 
 <https://github.com/mie-lab/trackintel/blob/master/examples/setup_example_database.py>`_.
 
diff --git a/trackintel/model/locations.py b/trackintel/model/locations.py
index a62969e..74daac8 100644
--- a/trackintel/model/locations.py
+++ b/trackintel/model/locations.py
@@ -1,9 +1,11 @@
 import pandas as pd
-
 import trackintel as ti
-import trackintel.preprocessing.filter
-import trackintel.visualization.locations
-import trackintel.visualization.staypoints
+import trackintel.io
+from trackintel.io.file import w
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Ensure trip list column is parsed correctly when reading tours from CSV
      List of trip ids in a tour dataframe is unstructured when reading it from csv.
similar to  #443 but easier to fix.
Tours store a list of the trips they aggregate in the field "trips". Unfortunately the datatype of this list is not stable when writing+reading it from a csv file.
This can be easily 
      Way: parse tour trip list column with literal eval converter
      - Inspect the CSV tour reading function and check how list columns like 'trips' are parsed when reading from CSV into a DataFrame.
      - In the CSV reading function for tours, configure `kwargs.setdefault('converters', {}).setdefault('trips', ast.literal_eval)` before calling `pd.read_csv` so that serialized trip lists are parsed into Python lists by default.
      - Run the tour CSV file IO tests to confirm round-tripped tours preserve the 'trips' column as a list.

=== TASK gleam-lang__gleam-3486 | repo gleam-lang/gleam | rust
--- Issue
Language Server: Label access completion only works with public types
The language server doesn't show any suggestion for fields when a record's type isn't public:

![image](https://github.com/user-attachments/assets/3d752e6b-71b2-48e0-ad07-b2b14bf45d7e)

If we set `Person` as public, suggestions show up.

![image](https://github.com/user-attachments/assets/06dd6878-e89a-4780-bd58-e4b246e957b7)

--- Real fix (for your judgement only)
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
+     
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Generate missing pattern matches including field labels
      Use labels when inserting missing patterns
Right now the "fill in missing patterns" code action adds constructors by putting placeholders `_` for each field, labelled or not. It would be really nice if it also automatically inserted the label name for labelled fields:
```gleam
type Wibble {
  Wibble
      Way: Add field labels to generated missing pattern matches
      - Locate the missing‑pattern generation code (compiler‑core/src/exhaustiveness/missing_patterns.rs) and the printer (compiler‑core/src/exhaustiveness/printer.rs). Run the test suite to see the failing label‑related tests.
      - Introduce a VariantField struct that stores an optional label (EcoString) and the variable. Change Term::Variant to hold a Vec<VariantField> instead of Vec<Variable>. In MissingPatternsGenerator, when handling RuntimeCheck::Variant, build VariantField values from the fields vector and the labels map supplied by the runtime check.
      - Update the printer to iterate over VariantField entries. For each field, output the label followed by ':' when present. When the field maps to an infinite term and a label is present, omit the placeholder (shorthand syntax). Add a space before the printed sub‑pattern only when a label precedes it.
      - Run the full repository test suite to ensure the new behaviour does not break other exhaustiveness or language‑server tests.
  [2] Goal: Fix Erlang code generation for block expressions in record updates
      Incorrect Erlang code with block pattern during record update
<!--
Please include the following:
- Steps and code to reproduce the problem
- Versions for Gleam, OS, the runtime and any other software used
- Logs from running Gleam with the `GLEAM_LOG` environment variable set to `trace`
-->
Hi
      Way: Wrap complex single-expression blocks in begin-end during Erlang codegen
      - Locate block expression generation in the Erlang code generator module.
      - Identify where single-statement blocks are simplified to parentheses `(...)` instead of `begin ... end`.
      - Update single-expression block handling in Erlang codegen to check `!needs_begin_end_wrapping(expression)` instead of only checking if it's a non-pipe statement, ensuring statements that emit variable assignments (like record updates) get wrapped in `begin ... end`.
      - Remove unused helper methods like `is_non_pipe_expression` from the typed statement AST.
      - Add regression test with nested record updates wrapped in block expressions and run compiler tests.
  [3] Goal: Make generate function action include argument labels
      "Generate function" code action doesn't take labels into account
At the moment if I have a function that doesn't exist and is also using labels like this: `wibble(wobble: 1)`, the "Generate function" code action will generate a function that has no labels and so will be invalid. It would be nice if 
      Way: include argument labels in generated functions from calls
      - Identify where undefined function calls trigger the 'Generate function' code action and inspect how function arguments are constructed.
      - Add a helper method `is_invalid()` to `TypedExpr` to detect invalid expression nodes.
      - Implement a helper to validate argument labels in call expressions, verifying that no duplicate labels exist and unlabelled arguments do not appear after labelled ones.
      - Update the AST visitor for `GenerateFunction` to intercept `visit_typed_expr_call` when the called expression is invalid and in range, extracting call argument labels.
      - Update code generation to include the label prefix in parameter definitions when present.
      - Run the language server code action test suite to verify generated function signature and label correctness.

=== TASK phpactor__phpactor-2343 | repo phpactor/phpactor | php
--- Issue
Generate @param refactoring not triggered for `iterable`
![image](https://user-images.githubusercontent.com/530801/218270747-b07486ea-4048-42fa-9477-53d9fcf8244f.png)

--- Real fix (for your judgement only)
diff --git a/doc/lsp/vim.rst b/doc/lsp/vim.rst
index aba6aabd..637e272e 100644
--- a/doc/lsp/vim.rst
+++ b/doc/lsp/vim.rst
@@ -22,7 +22,7 @@ Client Guides
 
             Plug 'neovim/nvim-lspconfig'
 
-        Then enable it in your ``.vimrc.`` (note that `on_attach` is a
+        Then enable it in your ``.vimrc``. (note that `on_attach` is a
         `callback
         <https://github.com/neovim/nvim-lspconfig#suggested-configuration>`_
         which can be used for key bindings):
diff --git a/lib/WorseReflection/Bridge/TolerantParser/Diagnostics/DocblockMissingReturnTypeProvider.php b/lib/WorseReflection/Bridge/TolerantParser/Diagnostics/DocblockMissingReturnTypeProvider.php
index bb8d1b1e..25dc672e 100644
--- a/lib/WorseReflection/Bridge/TolerantParser/Diagnostics/DocblockMissingReturnTypeProvider.php
+++ b/lib/WorseReflection/Bridge/TolerantParser/Diagnostics/DocblockMissingReturnTypeProvider.php
@@ -48,8 +48,7 @@ class DocblockMissingReturnTypeProvider implements DiagnosticProvider
             return;
         }
 
-        $docblock = $method->docblock();
-        $docblockType = $docblock->returnType();
+        $docblockType = $method->docblock()->returnType();
         $actualReturnType = $frame->returnType()->generalize();
         $claimedReturnType = $method->inferredType();
         $phpReturnType = $method->type();
@@ -74,12 +73,16 @@ class DocblockMissingReturnTypeProvider implements DiagnosticProvider
             return;
         }
 
-        if (
-            $claimedReturnType->isDefined() && !$claimedReturnType->isClass() && !$claimedReturnType->isArray() && !$claimedReturnType->isClosure()
+        if ($claimedReturnType->isDefined()
+            && !$claimedReturnType->isClass()
+            && !$claimedReturnType->isArray()
+            && !$claimedReturnType->isClosure()
+            && !$claimedReturnType->isIterable()
         ) {
             return;
         }
 
+
         if ($actualReturnType->isClosure()) {
             yield new Docb
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Improve type inference for foreach over union arrays
      Type information not used in foreach (type is union of empty array and typed array)
```
        $offsets = [];
        $highlights = [];
        foreach ($this->generate($source, $offset) as $highlight) {
            $offsets[] = $highlight->start;
            $offsets[] = $highlight->end;
   
      Way: Reduce union types in foreach loops by consuming empty arrays
      - Locate the type inference logic for `foreach` statements, specifically where the type of the iterated variable is determined and updated.
      - Identify the mechanism for simplifying or reducing union types. This involves finding the `reduce()` method on aggregate types.
      - Modify the `reduce()` method in `AggregateType` to iterate through its constituent types and remove any type that is 'consumed' by another type in the aggregate. This requires a new `consumes` method on the base `Type` class.
      - Implement the `consumes` method in `ArrayType` to specify that an `ArrayType` (e.g., `Highlight[]`) can consume an `ArrayLiteral` representing an empty array (`array{}`).
      - Apply the `reduce()` method to the resulting type when updating local variables after a `foreach` loop in `ForeachStatementResolver` to ensure union types are simplified.
      - Adjust the `AssignmentExpressionResolver` to correctly infer array types when elements are added using array addition (`$foo[] = $value`). If a literal value is assigned, add its specific type; otherwise, infer a generic array type for the element.
  [2] Goal: Enable auto-completion for enum instance properties and methods
      Auto-completion not working for Enum instances
Hello! Thanks for all the work getting traits to work in Enums!

However, I noticed that auto-completion for Enum properties and methods does not work outside of the Enum (and the auto-completion that does occur inside the Enum is incorrect). Note tha
      Way: Enable completion for enum instance properties and methods
      - Locate the code responsible for class member auto-completion, specifically where it filters types for completion suggestions.
      - Modify the type check in the class member completor to include `ClassLikeType` instead of just `ClassType`. This ensures that enum instance types are considered for member completion.
      - Identify the reflection logic for enum cases, specifically where their types are constructed.
      - Update the `TypeFactory` methods for creating enum case types (`enumBackedCaseType` and `enumCaseType`) to accept a `Reflector` instance. Pass this `Reflector` instance from `ReflectionEnumCase` when constructing the types.
      - Modify the `EnumBackedCaseType` to use the provided `Reflector` to dynamically add properties like `value` by reflecting a conceptual `BackedEnumCase` class and merging its properties into the enum case's members.
      - Run the full test suite to ensure the changes correctly enable enum instance completion without introducing regressions.
  [3] Goal: Recognize return types of __invoke() methods in analysis
      Recognize types returned by `__invoke()`
![image](https://user-images.githubusercontent.com/11404453/167432853-6c33f6f3-429e-4e46-bfd9-7946e6f7b706.png)
![image](https://user-images.githubusercontent.com/11404453/167433077-83215865-7487-4bc6-afb8-6261c312943d.png)

      Way: Detect and use __invoke return type for class call expressions
      - Locate the call expression resolver and understand how it currently resolves callable nodes, especially the special handling for Variable nodes.
      - Import ReflectedClassType and add logic after resolving the callable expression: if the resolved type is a ReflectedClassType and the class defines an __invoke method, return a NodeContext with the invoke method's return type.
      - Add isInvokable() and invokeType() methods to ReflectedClassType: isInvokable checks the reflected class for a __invoke method; invokeType returns the inferred type of that method or undefined if not found.
      - Run the project's test suite to ensure the new logic does not break existing behavior and that the previously failing tests now pass.

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
--- Real fix (for your judgement only)
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

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: RareLabelEncoder must ignore missing values when used with ColumnTransformer
      `RareLabelEncoder` with `missing_values`="ignore" does not work properly with `sklearn.compose.ColumnTransformer`
**Describe the bug**
An exception is raised with for no reason.

**To Reproduce**
```python
  import pandas as pd
  from sklearn.compose import ColumnTransformer
  from feature_en
      Way: Handle missing values and categorical types in rare label encoding
      - Reproduce the `TypeError` by running the provided Python script, which uses `RareLabelEncoder` with `missing_values='ignore'` inside a `ColumnTransformer` on a DataFrame containing `None` values.
      - In the `fit` method of `RareLabelEncoder`, ensure that `self.encoder_dict_[var]` is always explicitly converted to a `list` when assigned, specifically when `n_categories` is too high and all unique values are considered frequent. This prevents type mismatches when `[np.nan]` is later concatenated.
      - In `RareLabelEncoder.transform`, modify rare label replacement. For categorical columns, add `self.replace_with` to categories before assignment. Use `X.loc` with boolean indexing (`~X[feature].isin(...)`) to assign `self.replace_with` to values not in frequent categories (and `np.nan` if `missing_values='ignore'`). This applies to both `missing_values` branches.
      - Run the provided failing tests to confirm the fix.
  [2] Goal: Allow OrdinalEncoder to encode unseen categories as negative one
      `OrdinalEncoder` could output -1 for unseen categories
The OrdinalEncoder has an errors argument which can either raise an error or output NaNs when encountering new categories. For this particular class, it'd make sense to output -1 when a new category is encoded instead of generating NaNs.

      Way: support negative one encoding for unseen categories in ordinal encoder
      - Locate OrdinalEncoder and its handling of the unseen/errors parameter during initialisation and transform.
      - Update the parameter validation in OrdinalEncoder to accept 'encode' or the designated unseen handling value in addition to 'raise' and 'ignore'.
      - In the transform logic of OrdinalEncoder, fill unseen categories with -1 when the encoder is configured to encode unseen values instead of raising or setting to NaN.
      - Update docstrings and documentation for OrdinalEncoder reflecting the new option for encoding unseen categories as -1.
      - Run the encoder test suite to verify unseen handling and regression tests across encoders.
  [3] Goal: Validate variable types when column names are passed as strings
      no numerical or categorical variable checks when user passes string instead of list
`_find_or_check_numerical_variable`s and `_find_or_check_categorical_variables` don't check variable type when user passes string instead of list

Copying @dodoarg code that spots this issue:


Please have a loo
      Way: validate single and multiple column types in dataframe validators
      - Locate dataframe variable inspection functions `_find_or_check_numerical_variables` and `_find_or_check_categorical_variables`.
      - Reproduce the bug where passing a column name as a string or integer skips dtype validation instead of raising TypeError when the column dtype does not match expectations.
      - Import dtype validation helpers `is_numeric_dtype`, `is_categorical_dtype`, and `is_object_dtype` from `pandas.api.types` in `variable_manipulation.py`.
      - Update `_find_or_check_numerical_variables` to check `is_numeric_dtype(X[variables])` when `variables` is `str` or `int`, raise `TypeError` if invalid, handle empty list separately with a `ValueError`, and check `variables is None` explicitly.
      - Update `_find_or_check_categorical_variables` to check `is_categorical_dtype(X[variables]) or is_object_dtype(X[variables])` when `variables` is `str` or `int`, raise `TypeError` if invalid, handle empty list separately with a `ValueError`, and check `variables is None` explicitly.
      - In `SelectByTargetMeanPerformance`, align numerical dtype selection by using `include='number'` rather than `include=['float', 'integer']`.

=== TASK casey__just-2460 | repo casey/just | rust
--- Issue
Command line flag to exit without error if recipe not found
In some cases it's helpful to run `just` so that it silently exists with code 0 if requested recipe is not found.  Possible usecase: running same recipe in multiple directories (e.g. with GNU parallel) when in some of them the recipe is not defined, in which case we just want to skip the dir.

It is currently possible to do with NPM scripts, for example `npm run --ws --if-present <script>` will run *script* in every workspace where script is defined. 
--- Real fix (for your judgement only)
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
+++ 
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Prevent passing recipe name as positional argument in PowerShell
      `set positionnal-arguments` append target at the end of the command on Windows but not on Linux
Hi !
The title says it all. Here is a justfile to reproduce the issue.

```just
# Enable positional args
set positional-arguments

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
se
      Way: Control positional argument passing per recipe
      - Locate the code responsible for determining when to pass positional arguments to a recipe's shell command.
      - Introduce a new recipe attribute to explicitly enable positional arguments for individual recipes.
      - Implement a method to check if a recipe should take positional arguments, considering both global settings and the new per-recipe attribute.
      - Modify the recipe execution logic to use this new method when deciding whether to append the recipe name and other positional arguments to the shell command.
      - Add documentation for the new `[positional-arguments]` attribute and a warning about its interaction with PowerShell.
      - Run the provided tests to ensure the changes behave as expected.
  [2] Goal: Allow submodules to access main Justfile environment variables
      Can't access env vars defined in Justfile from submodule
It seems that version 1.42 introduced a change https://github.com/casey/just/pull/2810 that does not allow submodules to access env variables defined in the main Justfile. 

`Justfile`
```
mod test './test.justfile'

export a := "a"
```

`test
      Way: Correct variable scope propagation for Justfile modules
      - Understand that submodules are not inheriting exported environment variables from their parent Justfile, leading to unbound variable errors.
      - Locate the code responsible for loading Justfiles and their modules, specifically where the parsing context or scope is passed to the module loading function. The issue hints at a change around version 1.42 and the diff points to `src/justfile.rs`.
      - Analyze the variable being passed as the scope for module loading. The fix changes a parameter from `root` to `scope`. This indicates that the module loading was incorrectly using the initial root scope instead of the current, potentially modified, scope that includes exported variables.
      - Modify the Justfile loading implementation to pass the current parsing `scope` to the module loading function instead of the `root` scope. This ensures that exported variables defined in the parent Justfile are available to the submodule.
      - Verify that submodules can now correctly access exported environment variables from their parent Justfile.
  [3] Goal: Make just recognize OpenBSD as a supported platform
      Feature request: add attribute `openbsd`
Trying to compile a recent version of [optimism](https://github.com/ethereum-optimism/optimism/blob/op-node/v1.10.0/CONTRIBUTING.md#development-quick-start) on OpenBSD/adJ 7.6beta1 I found that it uses `just`.

On OpenBSD/adJ 7.6beta1, it was straightforwar
      Way: Add new operating system attribute to recipe selection
      - Identify the code responsible for defining and evaluating operating system-specific attributes for recipes. This typically involves an enum for attribute types and a method that checks which recipes are enabled based on the current OS.
      - Add a new variant to the `Attribute` enum for the new operating system (e.g., `Openbsd`). Ensure this new variant is included in any methods that list all possible attributes or convert from a discriminant.
      - Modify the `Recipe::enabled` method to check for the presence of the new OS attribute on a recipe. Integrate this check with Rust's `cfg!(target_os = "<new_os>")` macro to enable recipes specifically tagged for the new OS, or for generic Unix systems if applicable.
      - Update documentation (e.g., `README.md`) to reflect the newly supported operating system attribute.
      - Run the integration tests, especially those related to attributes and OS-specific behavior, to confirm that recipes are correctly enabled and parsed on the target platform.

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


--- Real fix (for your judgement only)
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

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Balance columns before column-span all elements
      Balance columns before "column-span: all"
Is the [column-span](https://www.w3.org/TR/css-multicol-1/#column-span) attribute correctly handled? In my experimentation it seems just ignored, but maybe I am missing something. I used it as a property of an img tag.
      Way: Balance multi-column layout segments before column-spanning elements
      - Locate multi-column layout handling where column balancing and spanning blocks are processed.
      - Create a regression test with a multi-column container containing a child with column-span: all and column-fill set to auto, verifying whether columns preceding the span element are balanced.
      - Update the column balancing condition in the layout routine to enable balancing not only when column-fill is 'balance', but also when the current column segment is not the final segment (i.e. followed by a column-spanning element).
      - Run the targeted column-span balance test to confirm columns balance before spanning elements.
  [2] Goal: Apply full word spacing to spaces at the end of text boxes
      Half of word-spacing to a space at the end of a TextBox
Hello, I get different results from Chrome and Weasyprint when trying to use word-spacing in text that ends with a space.  Weasyprint only adds half of word-spacing to these cases.

The following code illustrates the problem.

```html
<sty
      Way: Compensate text engine word spacing on text box boundaries
      - Locate where word-spacing letter attributes are applied to spaces in layout text boxes.
      - Check how the underlying text engine (Pango) distributes word-spacing when a space is located at the start or end boundary of a text box.
      - Handle single-space text boxes by appending a zero-width space and updating the layout text so word spacing can take effect.
      - Double the space spacing factor applied to boundary spaces (index 0 or last byte) to compensate for Pango only applying half spacing on boundaries.
      - Run text layout tests to verify word spacing width matches expected dimensions across fragmented text spans.
  [3] Goal: Ignore out-of-flow elements when determining page property values
      Problem with named pages and out of normal page flow elements
## Problem

When rendering following HTML with weasyprint, the CSS for `named-page` is not applied on the first page. The first page is blue (from the default `@page` rule), but it contains content of the named page.

```html
<html>
      Way: Exclude out-of-flow elements from box page value propagation
      - Identify where CSS page property values (start and end page values) are propagated through box tree hierarchies.
      - Reproduce the issue by creating a test with running or fixed elements preceding a named-page container and asserting that the first page gets the named-page style instead of default page values.
      - In the parent box page-value computation method, filter the box's children to exclude out-of-flow boxes (such as absolutely positioned, fixed, and running elements) before selecting the first and last child boxes for start and end page propagation.
      - Run the layout test suite to verify that out-of-flow elements no longer prevent the propagation of named page values to the page box.

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
--- Real fix (for your judgement only)
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

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Enable decoding of Map<String, String> from TOML
      error decoding map: key is not a valid map index
I tried this:

```kotlin
val d = mapOf("key" to "value")
val t = Toml.encodeToString(d)

Toml.decodeFromString<Map<String,String>>(t)
```
It throws an `IllegalArgumentException: key is not a valid map index`.

This seems so simple that maybe
      Way: Route map deserialization through TomlMapDecoder
      - Locate the Toml class decode methods and see they always delegate to TomlMainDecoder, then verify that a Map descriptor has StructureKind.MAP and currently triggers an illegal index error
      - Import kotlinx.serialization.descriptors.StructureKind, add a private decode helper in Toml that checks if deserializer.descriptor.kind == StructureKind.MAP and calls TomlMapDecoder.decode, otherwise calls TomlMainDecoder.decode; update both decodeFromString and decode overloads to use this helper
      - Modify TomlMainDecoder.decode signature to accept TomlFile (instead of generic TomlNode) and adjust its internal call accordingly; add necessary imports
      - Extend TomlMapDecoder: add constructors for TomlFile and TomlTable that set a fullTableKey, adjust error messages to use this key, and provide a companion object decode method that creates a decoder for a TomlFile and returns the deserialized value
      - Run the full test suite to ensure the map decoding test now passes and no existing test regresses
  [2] Goal: Correctly decode TOML lists of nested serializable classes
      Can't parse simple toml with List
I have the next code:
```
import com.akuleshov7.ktoml.Toml
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.serializer

@Serializable
data class Foo(val x: List<Bar>)

@Serializable
data cl
      Way: Validate top-level structures in TOML serialization with descriptive errors
      - Inspect encoder attribute handling and parent structure resolution during serialization when encoding root or unnamed values (such as bare lists or primitives).
      - Update encoder attribute key resolution to throw a descriptive TomlWritingException instead of an internal error when a key is missing on structures requiring an associated name.
      - Handle StructureKind.LIST in encoder structure validation by throwing a TomlWritingException explaining that root lists of objects (arrays of tables) cannot be serialized without an enclosing serializable container.
      - Remove unused internal encoding exceptions that have been superseded by specific user-facing write exceptions.
      - Run test suite to verify table array writing, primitive decoding, and serializer tests pass without internal error regressions.

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
--- Real fix (for your judgement only)
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
+++ b/common/src/main/ja
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Update release snapshot version to the next development cycle
      change version to 0.2.4-SNAPSHOT
- [ ] I have searched the [issues](https://github.com/alibaba/fescar/issues) of this repository and believe that this is not a duplicate.

### Ⅰ. Issue Description

change version to 0.2.4-SNAPSHOT

### Ⅱ. Describe what happened

  If there is an exception, p
      Way: Update Maven project and internal version constants for a new snapshot cycle
      - Identify all project files that define or reference the current release version, including the root Maven project file, child module Maven project files, and any hardcoded version constants in source code.
      - Update the `<version>` tag in the root `pom.xml` to the target snapshot version (e.g., `0.2.4-SNAPSHOT`).
      - Update the `<version>` tag within the `<parent>` section of all child module `pom.xml` files to match the new snapshot version of the parent.
      - Locate and update any hardcoded version strings in Java source files to reflect the new major.minor.patch version (e.g., `0.2.4`).
      - Run the project's test suite to ensure that the version changes have not introduced any regressions or build issues.
  [2] Goal: Update project version to 0.2.1-SNAPSHOT
      change version to 0.2.1-SNAPSHOT
- [ ] I have searched the [issues](https://github.com/alibaba/fescar/issues) of this repository and believe that this is not a duplicate.

### Ⅰ. Issue Description
change version to 0.2.1-SNAPSHOT

### Ⅱ. Describe what happened

  If there is an exception, ple
      Way: Synchronize project version in Maven POMs and Java code
      - Identify the target version string from the issue description, including whether it's a SNAPSHOT version.
      - Locate all Maven `pom.xml` files that define the project's version or inherit a parent version, including the root `pom.xml` and any submodule `pom.xml` files.
      - Update the `<version>` element in the identified `pom.xml` files to the new SNAPSHOT version. This includes the main project version and parent versions in submodules.
      - Identify any Java source files that hardcode the project version string, typically in a `Version` utility class.
      - Modify the hardcoded version string in the identified Java file to the new release version (without the '-SNAPSHOT' suffix).
      - Compile the project and run the provided test suite to ensure all version changes are correctly applied and no regressions are introduced.
  [3] Goal: Add Random and RoundRobin load balancing algorithms
      add Random and RoundRobin  LoadBalance
## Why you need it?
 Is your feature request related to a problem? Please describe in details
 implement Random and RoundRobin  LoadBalance

## How it could be?
A clear and concise description of what you want to happen. You can explain more about input of
      Way: Implement Random and RoundRobin Load Balancing Strategies
      - Locate existing load balancer classes and abstract base implementations in the discovery module.
      - Create a RandomLoadBalance class extending AbstractLoadBalance that selects an invoker using java.util.Random over invokers.size().
      - Implement RoundRobinLoadBalance using an AtomicInteger with thread-safe CAS modulo arithmetic (handling integer overflow) to select elements cyclically.
      - Run the project test suite to verify load balancing and core component tests pass without errors.

=== TASK katex__katex-1789 | repo KaTeX/KaTeX | js
--- Issue
Comment at end of line within environment causes parse error
In version 0.10.0 (no issue in 0.10.0-rc.1), I am getting parse errors when inside an environment I have a comment at the end of a line, followed by a line that starts with a command
```
\begin{aligned}
  a&=b+c \\ % comment
  \text{d}+e&=f
\end{aligned}
```
`KaTeX parse error: Expected & or \\ or \cr or \end at position 41: …\\ % comment \̲t̲e̲x̲t̲{d}+e&=f \end{a…`
--- Real fix (for your judgement only)
diff --git a/src/Lexer.js b/src/Lexer.js
index ae3d6c47..af74cd62 100644
--- a/src/Lexer.js
+++ b/src/Lexer.js
@@ -17,6 +17,7 @@ import SourceLocation from "./SourceLocation";
 import {Token} from "./Token";
 
 import type {LexerInterface} from "./Token";
+import type Settings from "./Settings";
 
 /* The following tokenRegex
  * - matches typical whitespace (but not NBSP etc.) using its first group
@@ -53,19 +54,26 @@ const tokenRegexString = `(${spaceRegexString}+)|` +  // whitespace
     `|${controlWordWhitespaceRegexString}` +          // \macroName + spaces
     `|${controlSymbolRegexString})`;                  // \\, \', etc.
 
-// These regexs are for matching results from tokenRegex,
-// so they do have ^ markers.
-export const controlWordRegex = new RegExp(`^${controlWordRegexString}`);
-
 /** Main Lexer class */
 export default class Lexer implements LexerInterface {
     input: string;
+    settings: Settings;
     tokenRegex: RegExp;
+    // category codes, only supports comment characters (14) for now
+    catcodes: {[string]: number};
 
-    constructor(input: string) {
+    constructor(input: string, settings: Settings) {
         // Separate accents from characters
         this.input = input;
+        this.settings = settings;
         this.tokenRegex = new RegExp(tokenRegexString, 'g');
+        this.catcodes = {
+            "%": 14, // comment character
+        };
+    }
+
+    setCatcode(char: string, code: number) {
+        this.catcodes[char] = code;
     }
 
     /**
@@ -85,6 +93,19 @@ export default class Lexer implements LexerInterface {
         }
         let text = match[2] || " ";
 
+        if (this.catcodes[text] === 14) { // comment character
+            const nlIndex = input.indexOf('\n', this.tokenRegex.lastIndex);
+            if (nlIndex === -1) {
+                this.tokenRegex.lastIndex = input.length; // EOF
+                this.settings.reportNonstrict("commentAtEnd",
+                    "% comment has no terminating ne
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Treat a trailing backslash as regular whitespace instead of error
      Error with single backslash at end of line
**Describe the bug:**
(Copying from [a fixed pandoc issue](https://github.com/jgm/pandoc/issues/4134):)

> In LaTeX you can use a backslash `\` followed by any whitespace to produce a regular space, as opposed to an inter-sentence space. The whitespace n
      Way: Treat trailing backslash as regular whitespace in the lexer
      - Locate the Lexer implementation and the failing tests that check space handling and backslash‑newline behavior.
      - Add a new regular expression that matches a backslash followed by one or more whitespace characters (including a newline) and insert it into the token‑matching regex before the generic control‑symbol pattern.
      - Update the token extraction logic to prioritize the new capture group (match[6]) for backslash‑whitespace, falling back to the existing groups for other tokens, and remove the old trimming of control‑word whitespace.
      - Run the KaTeX test suite, focusing on the two previously failing tests, to ensure the lexer now treats a trailing backslash as a space.
  [2] Goal: Fix KaTeX parse error for `\vdots` character
      \text{⋮} causes error message
\text{⋮} will cause an error message:
KaTeX parse error: Can't use function '\mathord' in text mode at position 1: \̲m̲a̲t̲h̲o̲r̲d̲{\varvdots\rule…
other ellipsis unicode characters seem to work fine.
      Way: enable macro components for both math and text modes
      - Identify the macro definition and the underlying symbols and functions it expands to (e.g., unicode character `\u22ee` mapping to `\vdots`, which expands to internal symbols and `\rule`).
      - Reproduce the error by rendering the macro or character within text mode (e.g., `\text{⋮}`).
      - Update the macro definition to avoid math-only wrappers like `\mathord` if the macro should work across modes, replacing them with mode-agnostic group braces.
      - Add symbol definitions for text mode alongside math mode for any internal helper symbols used by the macro (such as `\varvdots`).
      - Allow any underlying utility functions used by the macro (such as `\rule`) to be executed in text mode by setting `allowedInText: true` in their definition.
      - Run the test suite to verify that both the macro command and unicode character parse successfully in math and text modes.
  [3] Goal: Prevent KaTeX from logging character metrics warnings for CJK characters
      Warning: No character metrics for '中' in style 'Main-Regular'
When I write Chinese characters in a math code block:

```latex
$$
f(x) = 测试中文
$$
```

They will be rendered properly but I still got a warning like this in the console:

```node
katex.js:5308 No character metrics for '中' in st
      Way: Parse unsupported Unicode characters as text mode
      - Inspect character parsing logic where non-mathematical Unicode characters are handled when encountering characters without dedicated font metrics.
      - Change the parse node mode for fallback Unicode characters from the current parse mode (`this.mode`) to text mode (`'text'`), ensuring downstream metric lookup uses text fallback rules instead of logging missing math metrics.
      - Update missing character warning messages in `makeSymbol` to include the rendering mode for easier debugging.
      - Run test suite to verify that Unicode characters like CJK, Devanagari, and Georgian render without missing metric warnings outside \text{} blocks.

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
--- Real fix (for your judgement only)
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
+++ b/cmd
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Add API Hub validation rules to the registry check tool
      Registry tool: add registry rules for API Hub
Add API Hub checks to `registry check` command.

rule group | rule number | resource | field | type | severity | check
-- | -- | -- | -- | -- | -- | --
API Hub | 1000 | artifacts/apihub-lifecycle |   | Lifecycle | ERROR | required, must load into pro
      Way: implement registry lint rules for artifact and label validation
      - Inspect the lint framework under check/lint, noting how FieldRule passes field values to ApplyToField via reflection.
      - Ensure FieldRule.Apply passes unwrapped interface values (v.Elem().Field(i).Interface()) rather than reflect.Value to r.ApplyToField so rule handlers can cast field values directly.
      - Add rule 1000 checking for required API Hub artifacts ('apihub-lifecycle', 'apihub-taxonomies') and ensuring apihub-taxonomies deserializes and contains required taxonomy IDs ('apihub-target-users', 'apihub-style', 'apihub-team', 'apihub-business-unit', 'apihub-gateway').
      - Add rule 1001 verifying that for any resource with 'Labels', if a label key starts with 'apihub-', its value must match an element ID in the corresponding taxonomy fetched from project artifacts.
      - Add rule 1002 validating that artifacts declared with internal mime types successfully unmarshal into their proto message types.
      - Run the rule test suite to confirm internal mime type validation and related rules pass.
  [2] Goal: Validate parent resource existence before listing artifacts
      Existence check for parent resource is missing while listing artifcts.
I was able to list artifacts for an `ApiDeployment` and `ApiSpec` that doesn't exist.

```
curl http://x.x.x.x/v1/projects/krpate-test/locations/global/apis/test/deployments
{
 "apiDeployments": [
  {
   "name": "projects/
      Way: validating parent resource hierarchy before querying child artifacts
      - Identify storage layer functions responsible for listing child artifacts under hierarchical parents such as spec revisions and deployment revisions.
      - Inspect the parent identifiers to see if wildcard segments ('-') are present at any level of the hierarchy.
      - In ListSpecRevisionArtifacts, check existence of the most specific non-wildcard parent (project, API, version, spec, or spec revision) using GetProject, GetApi, GetVersion, GetSpec, or GetSpecRevision, returning any lookup error immediately.
      - In ListDeploymentRevisionArtifacts, check existence of the most specific non-wildcard parent (project, API, deployment, or deployment revision) using GetProject, GetApi, GetDeployment, or GetDeploymentRevision, returning any lookup error immediately.
      - Run the artifact response code tests to verify parent not found errors are returned.
  [3] Goal: Flatten bulk upload subcommands directly under the upload command
      Registry tool: flatten `bulk` out of `registry upload` and bring subcommands up.
After #925 is merged, the `registry upload` subcommand will consist of `csv` and the bulk uploaders. With `registry apply` becoming our recommended mechanism for adding information to the registry, I think it would be r
      Way: Flatten CLI subcommands by removing an intermediate command
      - Identify the intermediate command (e.g., 'bulk') and its nested subcommands (e.g., 'openapi', 'protos', 'discovery') that need to be moved up one level in the command hierarchy.
      - Modify the main parent command (e.g., 'upload') to directly add the previously nested subcommands. This involves updating the parent command's `AddCommand` calls.
      - Relocate the source files for the subcommands from their original nested package (e.g., `cmd/registry/cmd/upload/bulk/`) to the new parent package (e.g., `cmd/registry/cmd/upload/`). Update the package declarations within these files accordingly.
      - Move any common flags or helper functions (e.g., `getParent`) that were defined in the intermediate command's package to the new parent package or to the individual subcommands, ensuring they remain accessible and functional.
      - Remove the definition and source files of the now-empty intermediate command (e.g., `bulk`).
      - Update any documentation or examples that reference the old, nested command structure to reflect the new flattened structure.

=== TASK casey__just-1809 | repo casey/just | rust
--- Issue
completions and modules don't work together
The completions script uses `just --summary` to fetch a list of recipes. A recipe `bar` inside module `foo` is listed as `foo::bar`, however module recipes can't be invoked like this ("Justfile does not contain recipe `foo::bar`").

I'm not sure the best solution although I do personally find `foo::bar` easier syntax to read as `foo bar` in my mind looks like a recipe `foo` that takes a parameter `bar`. 

I wonder whether `foo::bar` should be added as additional valid syntax to run a module's recipe?
--- Real fix (for your judgement only)
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
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow submodules to access main Justfile environment variables
      Can't access env vars defined in Justfile from submodule
It seems that version 1.42 introduced a change https://github.com/casey/just/pull/2810 that does not allow submodules to access env variables defined in the main Justfile. 

`Justfile`
```
mod test './test.justfile'

export a := "a"
```

`test
      Way: Correct variable scope propagation for Justfile modules
      - Understand that submodules are not inheriting exported environment variables from their parent Justfile, leading to unbound variable errors.
      - Locate the code responsible for loading Justfiles and their modules, specifically where the parsing context or scope is passed to the module loading function. The issue hints at a change around version 1.42 and the diff points to `src/justfile.rs`.
      - Analyze the variable being passed as the scope for module loading. The fix changes a parameter from `root` to `scope`. This indicates that the module loading was incorrectly using the initial root scope instead of the current, potentially modified, scope that includes exported variables.
      - Modify the Justfile loading implementation to pass the current parsing `scope` to the module loading function instead of the `root` scope. This ensures that exported variables defined in the parent Justfile are available to the submodule.
      - Verify that submodules can now correctly access exported environment variables from their parent Justfile.
  [2] Goal: Prevent passing recipe name as positional argument in PowerShell
      `set positionnal-arguments` append target at the end of the command on Windows but not on Linux
Hi !
The title says it all. Here is a justfile to reproduce the issue.

```just
# Enable positional args
set positional-arguments

set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]
se
      Way: Control positional argument passing per recipe
      - Locate the code responsible for determining when to pass positional arguments to a recipe's shell command.
      - Introduce a new recipe attribute to explicitly enable positional arguments for individual recipes.
      - Implement a method to check if a recipe should take positional arguments, considering both global settings and the new per-recipe attribute.
      - Modify the recipe execution logic to use this new method when deciding whether to append the recipe name and other positional arguments to the shell command.
      - Add documentation for the new `[positional-arguments]` attribute and a warning about its interaction with PowerShell.
      - Run the provided tests to ensure the changes behave as expected.
  [3] Goal: Make just recognize OpenBSD as a supported platform
      Feature request: add attribute `openbsd`
Trying to compile a recent version of [optimism](https://github.com/ethereum-optimism/optimism/blob/op-node/v1.10.0/CONTRIBUTING.md#development-quick-start) on OpenBSD/adJ 7.6beta1 I found that it uses `just`.

On OpenBSD/adJ 7.6beta1, it was straightforwar
      Way: Add new operating system attribute to recipe selection
      - Identify the code responsible for defining and evaluating operating system-specific attributes for recipes. This typically involves an enum for attribute types and a method that checks which recipes are enabled based on the current OS.
      - Add a new variant to the `Attribute` enum for the new operating system (e.g., `Openbsd`). Ensure this new variant is included in any methods that list all possible attributes or convert from a discriminant.
      - Modify the `Recipe::enabled` method to check for the presence of the new OS attribute on a recipe. Integrate this check with Rust's `cfg!(target_os = "<new_os>")` macro to enable recipes specifically tagged for the new OS, or for generic Unix systems if applicable.
      - Update documentation (e.g., `README.md`) to reflect the newly supported operating system attribute.
      - Run the integration tests, especially those related to attributes and OS-specific behavior, to confirm that recipes are correctly enabled and parsed on the target platform.

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


--- Real fix (for your judgement only)
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
+			allErrs = append(allErrs, field.Invalid(fieldSpec.Child("kubernetesVersion"), c.Spec.KubernetesVersion, "unable to determine kubernetes ve
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Make Hubble configuration optional in Cilium networking cluster specification
      Inaccurate instructions for enabling Cilium
**1. What `kops` version are you running? The command `kops version`, will display
 this information.**
master

**2. What Kubernetes version are you running? `kubectl version` will print the
 version if a cluster is running or provide the Kubernetes v
      Way: make subspec pointer optional in kubernetes crd models
      - Locate the internal and versioned Go API definitions for the networking specification containing the sub-spec (HubbleSpec inside CiliumNetworkingSpec).
      - Change the sub-spec field in both internal and versioned API structs from a value type to a pointer type (*HubbleSpec) with omitempty in the JSON tag.
      - Update code generation artifacts (conversion and deepcopy routines) to handle nil pointer checks for the sub-spec field.
      - Update validation logic, options builder defaults, and template helper invocations to safely handle nil sub-spec pointer references without nil-dereference panics.
      - Run the API validation and model unit tests to verify valid specs without the sub-spec pass without errors.
  [2] Goal: Enable setting kops.EnvVar fields in cluster spec
      Failed to set env var when using kubetest2
/kind bug

**1. What `kops` version are you running? The command `kops version`, will display
 this information.**
Client version: 1.26.3 (git-v1.26.3)

**2. What Kubernetes version are you running? `kubectl version` will print the
 version if a clus
      Way: Enable reflection handling for kops.EnvVar in SetString
      - Locate the SetString implementation and its helper setType in util/pkg/reflectutils/access.go to understand why setting an EnvVar fails.
      - Add the kops API import and a new case for the type "kops.EnvVar" in setType. Parse the incoming string with strings.Cut("=") to fill the Name field and optionally the Value field, then return a reflect.Value of the constructed kops.EnvVar.
      - Run the unit tests that cover SetString, especially the env‑var sub‑tests, to verify the change works.
  [3] Goal: Enable pod outbound connectivity using Cilium ENI IPAM with secondary subnets
      [cilium - eni IPAM MODE]  problem with connection from POD to external resource in additional subnet after kops upgrade from 1.23 -> 1.25 
/kind bug

**1. What `kops` version are you running? The command `kops version`, will display
 this information.**

Version 1.23.2 (git-4125bbbe975ca104c609
      Way: Configure Cilium ENI IPAM with masquerading for outbound connectivity
      - Identify the cluster's networking configuration, specifically if Cilium is used with ENI IPAM.
      - Locate the kops validation logic for Cilium networking, specifically around the `Masquerade` setting when `ipam` is set to `eni`.
      - Correct the validation logic to require `Masquerade` to be enabled when Cilium ENI IPAM is used, instead of disabled.
      - Adjust the default behavior for Cilium's `Masquerade` and `EnableBPFMasquerade` settings. Ensure `Masquerade` defaults to enabled (unless IPv6-only) and `EnableBPFMasquerade` defaults to enabled specifically when ENI IPAM is active.
      - Update the Cilium addon template to use the correct configuration key `enable-ipv4-masquerade` instead of the deprecated `masquerade`.
      - Run the relevant kops tests to ensure the changes correctly enable outbound connectivity for pods using Cilium ENI IPAM with secondary subnets.

=== TASK netwo-io__apistos-108 | repo netwo-io/apistos | rust
--- Issue
Ability to exclude some structs 
Hello,

I am trying to use apistos and actix-identity at the same time. 
When I use

```
#[api_operation(tag = "another", summary = "Operator index endpoint")]
pub async fn test(user: Identity) -> HttpResponse {
```
It gives me error. 
"no function or associated item named `request_body` found for struct `actix_identity::Identity` in the current scope".

For successfully compile the code I have to add skip=true. But now my function is not documented at openapi. Please add ability to ignore variable name or structs. Ignoring Identity struct will solve my problem. 

--- Real fix (for your judgement only)
diff --git a/Cargo.toml b/Cargo.toml
index 0cbc892..6b2fb2c 100644
--- a/Cargo.toml
+++ b/Cargo.toml
@@ -22,7 +22,7 @@ documentation = "https://docs.rs/apistos/"
 license = "MIT OR Apache-2.0"
 rust-version = "1.75"
 publish = true
-version = "0.3.0"
+version = "0.3.1"
 
 [workspace.dependencies]
 actix-service = "2"
diff --git a/apistos-core/Cargo.toml b/apistos-core/Cargo.toml
index f8a4d73..694278a 100644
--- a/apistos-core/Cargo.toml
+++ b/apistos-core/Cargo.toml
@@ -28,7 +28,7 @@ serde_qs = { workspace = true, features = ["actix4"], optional = true }
 uuid = { workspace = true, optional = true }
 url = { workspace = true, optional = true }
 
-apistos-models = { path = "../apistos-models", version = "0.3.0", features = ["deserialize"] }
+apistos-models = { path = "../apistos-models", version = "0.3.1", features = ["deserialize"] }
 
 [dev-dependencies]
 assert-json-diff = { workspace = true }
diff --git a/apistos-gen/src/internal/mod.rs b/apistos-gen/src/internal/mod.rs
index 965680c..295e780 100644
--- a/apistos-gen/src/internal/mod.rs
+++ b/apistos-gen/src/internal/mod.rs
@@ -6,7 +6,7 @@ use proc_macro_error::{abort, emit_error};
 use quote::quote;
 
 use syn::{
-  Expr, FnArg, Ident, ImplGenerics, ItemFn, Lit, Meta, ReturnType, Token, Type, TypeGenerics, TypeTraitObject,
+  Expr, FnArg, Ident, ImplGenerics, ItemFn, Lit, Meta, Pat, ReturnType, Token, Type, TypeGenerics, TypeTraitObject,
   WhereClause,
 };
 
@@ -35,7 +35,7 @@ pub(crate) fn gen_open_api_impl(
       }
     )
   } else {
-    let args = extract_fn_arguments_types(item_ast);
+    let args = extract_fn_arguments_types(item_ast, &operation_attribute.skip_args);
 
     let deprecated = item_ast.attrs.iter().find_map(|attr| {
       if !matches!(attr.path().get_ident(), Some(ident) if &*ident.to_string() == "deprecated") {
@@ -216,14 +216,23 @@ pub(crate) fn gen_item_ast(
   (responder_wrapper, quote!(#item_ast))
 }
 
-fn extract_fn_arguments_types(item_ast: &ItemFn) -> Vec<Type> {
+fn extract_fn_arg
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Gate Actix web types and imports behind the Actix feature flag
      Usage of Actix-Specific Features Without Actix Feature Flag
I've noticed that Actix-specific components like `Either` are being used outside the scope of the `actix` feature flag, which could lead to issues in non-Actix environments where the feature is not enabled. 

For instance, in this snippet
      Way: Gate framework-specific types and dependencies behind Cargo feature flags
      - Check compilation of the target crate with default or empty features disabled to reproduce missing import/type errors.
      - Locate ungated imports and trait implementations using framework-specific types (such as `actix_web::Either` and schema types only needed for it).
      - In Cargo.toml, mark framework-tied dependencies (e.g., `pin-project`) as optional and include them under the corresponding feature flag.
      - Apply `#[cfg(feature = "...")]` conditional compilation attributes to the framework-specific imports and trait implementations.
      - Verify crate compilation succeeds both with the feature disabled and when running the full test suite with features enabled.

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
--- Real fix (for your judgement only)
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
-                // Add an
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Allow Markdown tables to render with empty cells
      Markdown table parsing fails on empty cells
Trying to print a markdown file that contains empty cells fails currently (mordant version 2.0.0-beta2). 
 
```kotlin
fun main() {
    val t = Terminal()
    t.println(Markdown("""
        | Test |
        |------|
        |      |
    """.trimInd
      Way: Prevent double-dropping leading and trailing whitespace on single-node lists
      - Trace markdown table cell parsing logic where whitespace tokens are trimmed from AST child nodes.
      - Reproduce the issue by creating a test that renders markdown containing a table with empty or whitespace-only cells.
      - Update the whitespace trimming helper function (`dropWs`) so that `dropLast` is only 1 when the list has more than one node and the last node is whitespace, preventing `drop` (1) and `dropLast` (1) from both applying to a single-node list.
      - Run the test suite to verify table rendering succeeds with empty cells.
  [2] Goal: Generate machine-readable table output for non-interactive use
      Table rendering on non-interactive output
Tables are rendered poorly on non-interactive output (e.g. redirect to file). The default width of 79 causes unwanted truncation of content.

It would be nice to be able to set "infinite" width for tables, so that they expand to fit all content in every ce
      Way: Configure terminal dimensions for non-interactive output
      - Identify that table output is truncated when redirected to a file or pipe because the terminal width defaults to 79 columns in non-interactive mode.
      - Locate the `Terminal` class constructor where terminal properties are initialized.
      - Add `nonInteractiveWidth` and `nonInteractiveHeight` parameters to the `Terminal` constructor call. Set `nonInteractiveWidth` to a large integer (e.g., `Int.MAX_VALUE`) to effectively provide 'infinite' width, or to a specific desired width for machine parsing.
      - Verify that the table output, when redirected, now respects the `nonInteractiveWidth` and `nonInteractiveHeight` settings, preventing truncation and allowing full content display.
  [3] Goal: Prevent progress bar rendering error with small lengths
      Progress bar: length must be 2 or greater, was 1
See https://github.com/russellbanks/Komac/issues/102:

GitHub runners sometimes produce this error with progress bars:

```
   Exception in thread "Timer-0" java.lang.IllegalArgumentException: length must be 2 or greater, was 1
  	at com.github.
      Way: Prevent progress bar pulse animation with insufficient width
      - Locate the `ProgressBar` widget's rendering logic, specifically the `makeComplete` function responsible for drawing the completed portion of the bar and its pulsing animation.
      - Understand that the pulsing animation relies on color interpolation, which fails when the segment length is too small (e.g., 1 character). The `IllegalArgumentException` indicates a minimum length requirement for the interpolation sequence.
      - Modify the condition that triggers the color interpolation for the pulsing effect. Add a check to ensure the `barLength` (the width of the completed segment) is at least 2 before attempting to apply the pulsing color transformation.
      - Run the provided tests to ensure the fix resolves the `IllegalArgumentException` and does not introduce regressions.

=== TASK fhir__sushi-334 | repo FHIR/sushi | ts
--- Issue
Building bundle with resources has order dependent behavior in build
If you try to write the Bundle first, then the included resources (in the same file), the FHIR build fails, but if invert the order, the build succeeds.

Sushi should order the resources based on their dependencies.

The build probably shouldn't break with a report about trying to generate the same resource twice during validation phase either, but that would probably be related to IG Builder 


--- Real fix (for your judgement only)
diff --git a/src/export/InstanceExporter.ts b/src/export/InstanceExporter.ts
index 2dfd826..ff2d9e8 100644
--- a/src/export/InstanceExporter.ts
+++ b/src/export/InstanceExporter.ts
@@ -286,6 +286,10 @@ export class InstanceExporter implements Fishable {
   }
 
   exportInstance(fshDefinition: Instance): InstanceDefinition {
+    if (this.pkg.instances.some(i => i._instanceMeta.name === fshDefinition.id)) {
+      return;
+    }
+
     const json = this.fisher.fishForFHIR(
       fshDefinition.instanceOf,
       Type.Resource,

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: SUSHI must warn and ignore duplicate FSH definitions by canonical URL
      SUSHI beta does not check for duplicate resources between input/* and input/fsh
If there is a FSH definition that has the same canonical URL as a JSON/XML resource already in one of the other `input/*`, SUSHI should issue a warning and ignore the FSH definition. Currently it still generates the FSH 
      Way: Warn and ignore duplicate FSH definitions by canonical URL
      - Understand that SUSHI should prevent FSH-generated resources from overwriting or duplicating pre-existing FHIR resources (JSON/XML) in the input directory, identified by their canonical URL, resource type, and ID.
      - Locate the code responsible for writing FHIR resources generated from FSH. This is typically where the final output is produced after FSH compilation.
      - Modify the `writeFHIRResources` function to accept the `FHIRDefinitions` object, which holds all loaded FHIR definitions, including those from `input/*`.
      - Implement logic within `writeFHIRResources` to retrieve all pre-defined resources from the `FHIRDefinitions` object. Before writing each FSH-generated resource, check if a resource with the same canonical URL, resource type, and ID already exists among the pre-defined resources. If a duplicate is found, log an error message and skip writing the FSH-generated resource.
      - Add new methods to the `FHIRDefinitions` class (`allPredefinedResources`, `fishForPredefinedResource`, `fishForPredefinedResourceMetadata`) to facilitate efficient retrieval and identification of resources loaded from `input/*`.
      - Update the `MasterFisher` to prioritize predefined resources when fishing for FHIR definitions or their metadata, ensuring that existing `input/*` resources are found first.
  [2] Goal: Export StructureDefinition differential with only newly introduced mappings
      Export StructureDefinition with only new mappings
The `StructureDefinition.mapping` element does not need to repeat all mappings from its parent. The IG Publisher will add in the parent mappings during processing. SUSHI should export StructureDefinitions with only the newly introduced mappings and t
      Way: export StructureDefinition with only new mappings unless snapshot requested
      - Identify where StructureDefinition mappings are initialized and serialized during export, noting that inherited mappings need to be separated from newly defined mappings.
      - Add an original mappings tracker to StructureDefinition, capturing the initial inherited mapping array with deep cloning right after the structure definition is initialized in the exporter.
      - Implement mapping serialization logic that computes the difference between current mappings and captured original mappings, exporting only new mappings by default and prepending new mappings before inherited mappings when snapshot mode is true.
      - Update JSON building in StructureDefinition to route the 'mapping' property through the new mapping serialization logic and remove empty mapping fields.
      - Run test suite to verify non-snapshot differentials only export new mappings, empty mappings are omitted, and snapshot mode prepends new mappings to original mappings.
  [3] Goal: Include resource extensions for Binary resources in IG JSON
      SUSHI drops resource extensions when generating IG JSON
To support "adjunct binary" loading with `Binary` resources, you must add an extension to the resource description in the IG JSON file.  You should be able to do this in sushi-config.yaml, but it seems that the extension is not coming through.
      Way: Ensure configured resource extensions are included in generated IG JSON
      - Identify the code path responsible for processing resource definitions from configuration and adding them to the IG JSON. The issue describes extensions defined in `sushi-config.yaml` not appearing in the output.
      - Reproduce the issue by configuring a resource (e.g., a Binary resource) with an extension in `sushi-config.yaml` and running SUSHI. Verify that the generated IG JSON (`ig.json`) does not contain the specified extension for that resource.
      - Modify the `IGExporter` class to explicitly copy the `extension` property from the `configResource` (the resource definition from `sushi-config.yaml`) to the `newResource` (the resource object being added to the IG JSON) at all points where resources are added or updated in the `ig.definition.resource` array.
      - Execute the provided tests to ensure the fix correctly integrates resource extensions without introducing regressions.

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
--- Real fix (for your judgement only)
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
+++ b/src/main/java/org/jenkinsci/plugins/prome
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Add a metric for the duration of currently running builds
      Metric to show duration of running build
### What feature do you want to see added?

We are looking for a way to create a Prometheus alert by writing an expression using metrics exposed under https://github.com/jenkinsci/prometheus-plugin/blob/master/docs/metrics/index.md. 

This Prometheus aler
      Way: Expose current build duration as a Prometheus metric
      - Identify the Jenkins plugin's metric collection entry point for jobs and builds.
      - Create a new Prometheus Gauge metric type specifically for tracking the duration of currently running builds.
      - Implement the logic within the new Gauge to calculate the duration of a build only if it is currently running.
      - Integrate the new Gauge into the existing `JobCollector` to ensure it is initialized and its metrics are collected.
      - Update the plugin's documentation to include the newly exposed metric.
      - Run the provided tests to verify the new metric is correctly integrated and does not introduce regressions.
  [2] Goal: Expose metrics to identify stuck jobs based on console log activity
      Metrics to identify stuck jobs
### What feature do you want to see added?

We would like a metric or metrics that would help us identify a job that is stuck.

We use jenkins jobs to run ansible playbooks over a large number of hosts, and in some sites the job might take a long time to run due to
      Way: expose job log activity and stuckness metrics for builds
      - Identify the collector types and factories handling Jenkins Job and Run metrics.
      - Define collector types for stuck build gauges and job log activity gauges in CollectorType enum.
      - Implement gauge collector classes for Run (build likely stuck based on executor or log activity) and Job (time since log updated).
      - Register the new gauge collectors in the job/run collector factories and wire them into the main job collector lifecycle (initialization, sample gathering, and calculation).
      - Run the build and collector test suite including the stuck gauge tests.
  [3] Goal: Ensure all Prometheus metrics use the configured namespace
      Issues with namespace
              @Waschndolos we are currently testing this fix and surprinsingly, we now have an issue on the "namespace" (or "prefix") of some metrics that are still using the default namespace/prefix instead of using the one we configured in the options... 
could it be related
      Way: Ensure Prometheus metrics reflect configured namespace
      - Identify metrics that are not respecting the configured Prometheus namespace, specifically those managed by `CounterManager`.
      - Examine the `CounterManager` and its internal `CounterEntry` class to understand how metric identities are determined.
      - Modify the `CounterEntry` class to include the configured namespace as part of its identity.
      - Run the tests for `CounterManager` to ensure the changes correctly handle metric identification with varying namespaces.

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
--- Real fix (for your judgement only)
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

--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Fix Series rename_axis raising AttributeError
      BUG: Series.rename_axis raises AttributeError
### Modin version checks

- [x] I have checked that this issue has not already been reported.

- [x] I have confirmed this bug exists on the latest released version of Modin.

- [x] I have confirmed this bug exists on the main branch of Modin. (In order 
      Way: Implement missing _set_axis_name helper on Series class
      - Locate where `Series.rename_axis` or `BasePandasDataset.rename_axis` delegates to `_set_axis_name`.
      - Reproduce the `AttributeError: 'Series' object has no attribute '_set_axis_name'` by calling `rename_axis` on a Series.
      - Implement `_set_axis_name` on the `Series` class to validate the axis index via `_get_axis_number`, set the index names on self or a copy depending on `inplace`, and return the modified object or `None`.
      - Run the series tests to verify `Series.rename_axis` now works correctly.
  [2] Goal: Apply docstring extensions without reloading pandas modules
      BUG: using docstring extensions causes several incorrect behaviors because DataFrame, Series, and BasePandasDataset cannot recognize each other
### Modin version checks

- [X] I have checked that this issue has not already been reported.

- [X] I have confirmed this bug exists on the latest released
      Way: update inherited docstrings in place on config change without reloading modules
      - Locate the module reload behavior triggered when setting docstrings via environment variable config class, and trace how class identity breaks across reloads.
      - Reproduce the class identity bug by mutating the doc module setting at runtime and verifying class instance checks fail after reload.
      - Remove module reloading logic from the config setter class (`DocModule.put`).
      - Implement in-place docstring updating logic in utility functions by storing docstring inheritance callbacks and registering a subscriber hook on doc module config changes.
      - Run the test suite to confirm docstrings update properly and class instances maintain type identity.
  [3] Goal: Fix Series argmin to return correct position for non-standard index
      BUG: argmin returns incorrect results
### Modin version checks

- [X] I have checked that this issue has not already been reported.

- [X] I have confirmed this bug exists on the latest released version of Modin.

- [X] I have confirmed this bug exists on the main branch of Modin. (In order to do th
      Way: fix-series-argmin-argmax-positional-indexing-for-custom-indices
      - Locate where `Series.argmin` and `Series.argmax` are implemented in the base pandas Series API wrapper.
      - Reproduce the issue by calling `.argmin()` on a Modin Series initialized with a non-range index (e.g., `index=[1, 0, 2]`) and comparing output against pandas.
      - Update `argmin` and `argmax` in the Series object to compute positional index correctly or return positional integer offset matching pandas behaviour.
      - Run test suite for Series indexing and reduction operations to verify fix.

=== TASK urlaubsverwaltung__urlaubsverwaltung-4858 | repo urlaubsverwaltung/urlaubsverwaltung | java
--- Issue
Überstundenabbau über Api abfragbar

**Is your feature request related to a problem? Please describe.**
Damit Mitarbeiter kummuliert über einen bestimmten Zeitraum wissen, wie viele Überstunden sie abgebaut haben, braucht das Tool die Anzahl an Stunden/Minuten welche in einer Abwesenheit  vom Type `Überstundenabbau`  konfiguriert wurden, in der API.

**Describe the solution you'd like**
Da aktuell die Api `/api/persons/{personId}/absences` schon benutzt wird, wäre es dufte, wenn diese Information in diese API mitgegeben wird. Es könnte z.B. in einem optionalem Feld `hours` oder `duration` eingeführt werden. Dafür könnte `java.time.Duration` verwendet werden. 

**Optionally describe alternatives you've considered**
Eine separate API in der Details zu einem bestimmten Abwesenheit abgefragt werden können. Das würde funktionieren, jedoch müsste das Tool dann für jede Abwesenheit vom Typ `Überstundenabbau` nochmal separat die Stunden/Minuten abfragen. Das ist in meinen Augen ineffizienter, aber dafür wäre die API "sauberer".


--- Real fix (for your judgement only)
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
+     
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Correctly calculate half-day sickness reports on special days
      Krankmeldungen fehlerhaft, wenn am 24.12. bzw. 31.12. eine halbe Krankmeldung eingereicht wurde und Arbeitsdauer der Tage halbtags ist
Wenn eine Krankmeldung am 24.12. oder am 31.12. halbtägig beantragt wurde, dann halbieren wir den halben Tag erneut. Dies bedeutet, dass jede Person, welche am 24.12
      Way: Prevent double-halving of half-day sick notes on special holidays
      - Locate the `getWorkDays` method within the `SickNote` entity, which is responsible for calculating the number of working days for a sick note.
      - Understand the existing logic: for each day in the sick note's date range, if the sick note is for a half-day (`dayLength.isHalfDay()`), the working time for that day is divided by two before being added to the total.
      - Modify the condition for dividing the working time by two. Introduce a check to ensure that this division only occurs if the day is NOT a half-day public holiday. This requires retrieving the `WorkingDayInformation` for the current `localDate` within the loop and checking its `hasHalfDayPublicHoliday()` status.
      - Run the provided test suite to ensure the fix correctly addresses the issue without introducing regressions.
  [2] Goal: Sort time-off history records by newest entry first
      Verlauf von Abwesenheiten, Krankmeldungen und Überstunden sind nicht korrekt Sortiert
**Describe the bug**

Der Verlauf von Abwesenheiten, Krankmeldungen und Überstunden sind nicht korrekt Sortiert. Aktuell erscheint, dass es keine Reihenfolge gibt. Wir möchten zunächst den Verlauf anhand ihrer vorl
      Way: Sort Spring Data repository comment queries by ID descending
      - Identify repository interfaces and services responsible for querying history and comments for applications, overtime, and sick notes.
      - Update Spring Data repository query method signatures to append `OrderByIdDesc` (e.g. `findByApplicationIdOrderByIdDesc`, `findByOvertimeOrderByIdDesc`, `findBySickNoteIdOrderByIdDesc`).
      - Update corresponding service classes to call the renamed repository methods when fetching comments.
      - Optionally implement `toString` methods on comment entity models for improved debugging.
      - Run the test suite to verify the comments are returned in descending ID order and no repository method binding errors occur.
  [3] Goal: Correctly display German translations for de_DE locale
      Fehlerhafte Darstellung individuelle Abwesenheitsarten bei Auswahl von Locale de_DE
Wenn im Browser die Locale `de_DE` statt `de` ausgewählt ist, dann wird folgender Platzhalter angezeigt, statt der deutschen Übersetzung.
![grafik](https://github.com/urlaubsverwaltung/urlaubsverwaltung/assets/14210
      Way: Fall back to language-only locale in localized map lookups
      - Identify where localized text entries are resolved from locale-keyed maps for specific entity types.
      - Add a fallback step when resolving the label by exact Locale: if exact match is not found or empty, look up using `Locale.forLanguageTag(locale.getLanguage())` before falling back to default message bundle strings.
      - Run the unit tests to verify that locale variants like 'de_DE' properly resolve strings stored under language-only locales like 'de'.

=== TASK styled-components__babel-plugin-styled-components-6 | repo styled-components/babel-plugin-styled-components | js
--- Issue
Support different import names
Currently, this breaks the babel plugin:

```JS
import s from 'styled-components'
```

Instead of hard-coding and checking for the `styled` keyword, we should be looking for the import declaration and getting the variable name. (what I do [here](https://github.com/styled-components/stylelint-processor-styled-components/blob/9d904c963466e397de2d08f84314249d9c0e7ce9/src/index.js#L60-L63) and [here](https://github.com/styled-components/stylelint-processor-styled-components/blob/9d904c963466e397de2d08f84314249d9c0e7ce9/src/utils/parse.js#L5-L23) in the stylelint processor)
--- Real fix (for your judgement only)
diff --git a/src/index.js b/src/index.js
index 564bc44..64c18f3 100644
--- a/src/index.js
+++ b/src/index.js
@@ -3,19 +3,35 @@ import template from 'babel-template'
 import hash from './utils/hash'
 import getTarget from './utils/get-target'
 
-const buildStyledCall = template(`styled({
+const buildStyledCall = template(`STYLED({
   target: TARGET,
   displayName: DISPLAYNAME,
   identifier: IDENTIFIER
 })`)
 
-const isStyled = (tag) => (tag.object && tag.object.name == 'styled') || (tag.callee && tag.callee.name == 'styled')
+// Default imported variable name to "styled", adjust based on import below
+let importedVariableName = 'styled'
+
+const isStyled = (tag) => (tag.object && tag.object.name === importedVariableName) || (tag.callee && tag.callee.name === importedVariableName)
 
 let id = 0
 
 export default function({ types: t }) {
   return {
     visitor: {
+			ImportDeclaration: {
+				enter(path) {
+					// Is the styled-components import!
+					if (path.node.source.value === 'styled-components') {
+						// If the default is imported it's at defaultImport[0], otherwise defaultImport is empty
+						const defaultImport = path.node.specifiers.filter((specifier) => t.isImportDefaultSpecifier(specifier))
+						if (defaultImport.length > 0) {
+							// Save the imported name
+							importedVariableName = defaultImport[0].local.name
+						}
+					}
+				}
+			},
       TaggedTemplateExpression: {
         enter(path, { opts }) {
           const addDisplayName = (opts.displayName === undefined || opts.displayName === null) ? true : opts.displayName
@@ -64,8 +80,8 @@ export default function({ types: t }) {
           // Prefix the identifier with a character if no displayName exists because CSS classes cannot start with a number
           const identifier = `${displayName || 's'}-${hash(`${id}${displayName}`)}`
           // Put together the final code again
-          // Create the styled({ }) call
           const call = buildStyledCall({
+            STYL
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Update styled-components private API to use .withConfig syntax
      Update to new syntax
As of https://github.com/styled-components/styled-components/pull/227 the new syntax for the private API is `.withConfig`:

```JS
const Box = styled.div.withConfig({ displayName: 'Box', componentId: 'Button_asdf123' })`
  styles: here;
`
```

*This avoids issues on React
      Way: Migrate styled-components Babel plugin to .withConfig API
      - Understand the new styled-components private API, which replaces direct configuration arguments with a `.withConfig` method call on the styled component factory. The `target` property is no longer used, and `identifier` is renamed to `componentId`.
      - Locate the Babel plugin's visitor logic responsible for transforming styled component definitions. Identify where the configuration object for `displayName` and `identifier` (now `componentId`) is constructed and applied.
      - Remove the `get-target` utility file and its import, as the `target` property is deprecated and no longer needed in the new API.
      - Modify the Babel plugin's transformation logic. Replace the old configuration (e.g., `styled({ target, displayName, identifier })`) with a `.withConfig` method call. The original `tag` (e.g., `styled.div`) becomes the callee. The new config object uses `displayName` and `componentId` (from `identifier`), omitting `target`.
      - Run the relevant tests to ensure that display names and component identifiers are correctly generated and applied using the new `.withConfig` syntax.
  [2] Goal: Determine unique component IDs reliably in Babel transforms
      Need better way to determine componentId
I was able to get duplicates with `create-react-app` setup because it uses babel-loader with cache enabled.

Considering other options.
- `path.hub.file.code`
- `path.hub.file.sourceFileName` and last part of `path.hub.file.sourceRoot`
- `path.hub.file.f
      Way: Generate unique component IDs in Babel transforms using file context
      - Identify the Babel visitor function responsible for generating component IDs and display names. This function typically processes styled component declarations.
      - Replace the global, incrementing component ID with a file-scoped counter. This counter should be stored in the Babel `state.file` object to persist across component visits within the same file.
      - Implement a mechanism to calculate a stable hash for each source file. This hash should incorporate elements that uniquely identify the file within its project context, such as the module name (from `package.json`), the file's path relative to its module root, and the file's content. Cache this hash on the `state.file` object to avoid redundant calculations.
      - Modify the component ID generation logic to combine the stable file hash with the file-scoped component counter. Ensure the resulting ID is a valid CSS class name by handling cases where the hash might start with a digit.
      - Update the main visitor function to pass the Babel `state` object to the `getDisplayName` and `getComponentId` functions, as they now rely on `state.file` for their operations.
      - Run the provided tests to ensure the new component ID generation is correct and does not break existing functionality.

=== TASK fika-lang__fika-37 | repo fika-lang/fika | elixir
--- Issue
Allow Elixir-style string interpolation
Allow using `#{}` inside strings which will be expanded and replaced with the string it contains. The expression inside `#{}` should be of string type.
--- Real fix (for your judgement only)
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
-    |> repeat(choice([string("\\\""), utf8_cha
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Support union types in function signatures and conditional expressions
      Add support for union types
A union type is a set consisting of multiple non-overlapping types. With union types, we can have function signatures of the type:

```elixir
fn foo : {:ok, Int} | {:error, String} do
  if true do
    {:ok, 123}
  else
    {:error, "Something went wrong"}
  end
e
      Way: Parse and Infer Union Types in Function Signatures and Conditionals
      - Create a Union type struct (e.g., `Fika.Types.Union`) that stores unique member types in a `MapSet`, provides a constructor `new/1` which flattens nested union structures, and implements `String.Chars` joining member types with ' | '.
      - Update the type parser grammar to define a base type parser and parse union types delimited by '|', converting them into AST union type nodes via helper functions. Also ensure tuple types properly parse inner elements into tuple AST structures.
      - Fix function definition argument parsing if recursion or repeat combinators cause parsing issues with multi-argument signatures.
      - Update conditional block type inference (`infer_if_else_blocks`) so that differing branch types are unified into a `Union` type instead of returning a type mismatch error.
      - Run the test suite to verify union type parsing and conditional expression type inference pass.

=== TASK godbus__dbus-297 | repo godbus/dbus | go
--- Issue
Prevent encoding errors
As seen in e.g. #244, #240, #205, it's relatively easy for application code to create messages that are ill-formatted semantically, and thus trigger a disconnection by the bus daemon. We should check this directly in library code to prevent one wrong message from terminating the whole connection, which would also result in better error messages:
1. Ensure that message.IsValid covers everything that already is checked in encoder.go
2. Check for valid format for error and signal names
3. Check IsValid in Emit
Method replies are a not-so-nice case because there's no real way to give feedback from the library to the application code here when using Export (without an API break), but just writing an error to stderr (with the option to disable it) would be an alternative.
--- Real fix (for your judgement only)
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
 	msg.
--- Kel's top 3 (earlier fixes from THIS repository)
  [1] Goal: Decode UnixFDIndex fields nested inside structs in DBus messages
      UnixFDIndex fields inside structs missed by ReadMessage()
I had a method return type with UnixFDs inside a struct, and wasn't getting the right fd numbers. I debugged the issue and found that the UnixFDIndex fields weren't getting translated by:
https://github.com/godbus/dbus/blob/85c14741/transpor
      Way: Correctly decode and encode nested Unix FDs in DBus messages
      - Identify that Unix file descriptor indices (UnixFDIndex) within nested structures (like structs) are not being correctly translated into actual file descriptors (UnixFD) during DBus message decoding, and that encoding also needs to manage these FDs.
      - Modify the `decoder` struct and its constructor `newDecoder` to accept and store a slice of file descriptors (`[]int`).
      - Update the `decoder.decode` method's handling of the 'h' (UnixFD) signature. Instead of always returning `UnixFDIndex`, it should attempt to retrieve the actual file descriptor from the `dec.fds` slice using the decoded index. If the index is valid, return `UnixFD`; otherwise, fall back to `UnixFDIndex`.
      - Introduce a new `DecodeMessageWithFDs` function that takes an `fds []int` argument and passes it to `newDecoder`. Update the original `DecodeMessage` to call this new function with an empty `fds` slice.
      - Modify the `encoder` struct and its constructors (`newEncoder`, `newEncoderAtOffset`) to accept and store a slice of file descriptors (`[]int`).
      - Update the `encoder.encode` method to handle `UnixFD` types. When encoding a `UnixFD`, it should append the actual file descriptor to the `enc.fds` slice and encode its index (position in the slice) as a `uint32`.