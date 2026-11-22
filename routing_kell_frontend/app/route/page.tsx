import { redirect } from "next/navigation";

/** The old /route page is now /task. Keep the URL working for bookmarks and external links. */
export default function RoutePage() {
  redirect("/task");
}
