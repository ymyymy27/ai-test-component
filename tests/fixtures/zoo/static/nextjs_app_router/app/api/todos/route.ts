import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({ todos: [] });
}

export async function POST(req: Request) {
  const body = await req.json();
  return NextResponse.json({ id: "t1", title: body.title }, { status: 201 });
}
