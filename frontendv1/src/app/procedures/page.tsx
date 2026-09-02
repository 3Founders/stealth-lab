import { SearchBox } from "@/components/search-box";

export default function ProceduresIndexPage() {
  return (
    <div className="pt-24">
      <h1 className="text-2xl font-semibold tracking-tight">Procedures</h1>
      <p className="mt-2 text-sm text-neutral-500">
        Search for the best known way to do something.
      </p>
      <div className="mt-8 max-w-xl">
        <SearchBox />
      </div>
    </div>
  );
}
