import Link from "next/link";

export default function NotFound() {
  return (
    <div className="pt-10 text-center">
      <h1 className="text-lg font-semibold">Not found</h1>
      <Link href="/" className="mt-2 inline-block text-sm text-s1">Back to Today</Link>
    </div>
  );
}
