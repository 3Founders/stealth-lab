-- 159_room_admin_role.sql
--
-- Next free number: 160 (158 is org provider key declarations).
--
-- What this adds: a third room role, 'admin'. The owner can promote a member to admin and demote them back; an admin can invite
-- people, remove plain members and delete other people's messages, but cannot rename or delete the room, remove the owner or another
-- admin, or change roles (services/rooms.py). Additive and idempotent: the old CHECK is replaced by one that also allows 'admin'.

ALTER TABLE room_members DROP CONSTRAINT IF EXISTS room_members_role_check;
ALTER TABLE room_members ADD CONSTRAINT room_members_role_check CHECK (role IN ('owner', 'admin', 'member'));
