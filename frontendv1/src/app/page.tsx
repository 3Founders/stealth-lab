import Link from "next/link";

import { SearchBox } from "@/components/search-box";

const EXAMPLES = [
  {
    title: "Reduce coding-agent context",
    meta: "Verified procedure",
  },
  {
    title: "Explore a large repository",
    meta: "12 implementations",
  },
  {
    title: "Run an autonomous coding loop",
    meta: "8 verified variants",
  },
];

export default function HomePage() {
  return (
    <div className="flex flex-col items-center pt-32 md:pt-44">
      <h1 className="text-3xl font-semibold tracking-tight text-neutral-900">
        Stealth Lab
      </h1>
      <p className="mt-3 text-base text-neutral-500">
        Find the best way to do something.
      </p>

      <div className="mt-10 w-full max-w-xl">
        <SearchBox />
      </div>

      <div className="mt-24 w-full max-w-xl">
        <h2 className="text-sm font-medium text-neutral-700">
          Trending ways to solve things
        </h2>
        <ul className="mt-3 divide-y divide-neutral-100">
          {EXAMPLES.map((ex) => (
            <li key={ex.title}>
              <Link
                href={`/search?q=${encodeURIComponent(ex.title)}`}
                className="flex items-baseline justify-between py-3 transition-colors hover:text-neutral-950"
              >
                <span className="text-sm text-neutral-800">{ex.title}</span>
                <span className="text-xs text-neutral-400">{ex.meta}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
