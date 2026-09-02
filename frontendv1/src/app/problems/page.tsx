export default function ProblemsPage() {
  return (
    <div className="pt-24">
      <h1 className="text-2xl font-semibold tracking-tight">Problems</h1>
      <p className="mt-2 max-w-md text-sm text-neutral-500">
        Active problems, demand signals, and rewards will appear here once the
        Problems backend is available.
      </p>
      <div className="mt-10 rounded-lg border border-dashed border-neutral-200 py-16 text-center">
        <p className="text-sm text-neutral-500">
          Problems are not supported by the backend yet.
        </p>
        <p className="mt-1 text-sm text-neutral-400">
          This page will light up when the API ships.
        </p>
      </div>
    </div>
  );
}
