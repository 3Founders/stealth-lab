import WayPage from "@/components/way/WayPage";

/**
 * One Way at /way/[id]. Client-rendered: the record is per-account, so there is nothing to pre-render and
 * nothing to index. The id is handed to the component rather than read inside it so the page stays a plain
 * function of its route (Next 15 makes `params` a promise).
 */
export const metadata = {
  title: "A Way",
  robots: { index: false, follow: false },
};

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <WayPage id={id} />;
}
