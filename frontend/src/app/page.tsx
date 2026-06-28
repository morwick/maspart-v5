"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getToken, getUser, landingPath } from "@/lib/auth";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace(getToken() ? landingPath(getUser()) : "/login");
  }, [router]);
  return (
    <main className="flex-1 grid place-items-center text-sm text-zinc-500">
      Memuat…
    </main>
  );
}
