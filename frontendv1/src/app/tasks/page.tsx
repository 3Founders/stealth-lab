import { SearchBox } from "@/components/search-box";

export default function TasksIndexPage() {
  return (
    <div className="pt-24">
      <h1 className="text-2xl font-semibold tracking-tight">Tasks</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Reusable capabilities and the implementations that can execute them.
      </p>
      <div className="mt-8 max-w-xl">
        <SearchBox />
      </div>
    </div>
  );
}
