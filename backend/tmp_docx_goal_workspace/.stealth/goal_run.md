# goal_run.md -- GENERATED, not canonical. Do not hand-edit.
# GOAL_RUN|<execution_id>|<outcome>
# GOAL_NODE|<goal_id>|<kind>|<status>|<goal_name>|binding=<binding_kind_or_->|proc=<procedure_id_or_->|verify=<verification_state_or_->|human_intervention=<bool>|resumed=<bool>
# ARTIFACT|<goal_id>|<filename>|sha256=<sha256>|size=<size_bytes>

GOAL_RUN|-|planned

GOAL_NODE|01a0cd0b-0a83-7cc0-92bc-39c4da824cb1|human|needs_input|Write a Node.js script that utilizes the `docx` library.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0cd00-6342-7219-be9f-0d692a6f3fe9|human|needs_input|Include the `docx` library in the script using `require('docx')`.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0cd00-ede9-75d2-87d5-0f03e1c2f7f9|human|needs_input|Configure page size for US Letter if needed, setting width to 12240 DXA and height to 15840 DXA.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|-|human|needs_input|Set page orientation to landscape by passing portrait dimensions and `orientation: PageOrientation.LANDSCAPE`.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0cd0f-0073-7bfd-b97f-d538c6804a56|human|needs_input|Define table column widths using `columnWidths` on the table and `width` on every cell, both in `WidthType.DXA`, ensuring column widths sum to the table width.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0cd02-ad2c-7866-8ffa-ca2e980b92f7|human|needs_input|For table shading, use `ShadingType.CLEAR`.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0cd03-8ba8-719d-a304-a68fab304974|human|needs_input|For lists, use a `numbering` configuration with `LevelFormat.BULLET` instead of literal bullet characters.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0cd04-2956-7280-8b9f-9dc02740ec4e|human|needs_input|For images, ensure `ImageRun` includes a `type` property (e.g., "png", "jpg").|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0cd04-94c1-71e1-b5aa-e05c7db159df|human|needs_input|Place `PageBreak` elements inside `Paragraph` elements.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0cd10-5c95-77c6-9c22-206bc20dc014|human|needs_input|Use separate `Paragraph` elements for new lines instead of `\n` characters.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0cd10-ca8d-7097-936d-fe4b036e8973|human|needs_input|For Table of Contents (TOC) entries, use built-in `HeadingLevel.*` for headings, or set `outlineLevel` for custom heading styles.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|01a0c78f-0ec2-7ce6-8a7a-82140446e3a3|human|needs_input|Use a paragraph bottom border instead of a table for horizontal rules.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
GOAL_NODE|-|human|needs_input|Implement dot-leader or right-aligned text on the same line using `PositionalTab` (with `alignment: PositionalTabAlignment.RIGHT` and `leader: PositionalTabLeader.DOT`) inside a `TextRun`.|binding=-|proc=-|verify=-|human_intervention=False|resumed=False
