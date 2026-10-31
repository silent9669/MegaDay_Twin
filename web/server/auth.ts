export type AccessEnv = {
  ADMIN_PASSWORD_HASH?: string;
  SESSION_SECRET?: string;
  LOGIN_LIMITER?: {
    limit(input: { key: string }): Promise<{ success: boolean }>;
  };
};
const COOKIE = "megaday_session";
export function sessionExpiry(request: Request) {
  return (
    Number(
      request.headers
        .get("cookie")
        ?.split(";")
        .map((v) => v.trim())
        .find((v) => v.startsWith(`${COOKIE}=`))
        ?.slice(COOKIE.length + 1)
        .split(".")[1] ?? 0,
    ) * 1000
  );
}
const bytes = (s: string) => new TextEncoder().encode(s);
const encode = (b: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(b)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
const decode = (s: string) =>
  Uint8Array.from(atob(s.replaceAll("-", "+").replaceAll("_", "/")), (c) =>
    c.charCodeAt(0),
  );
async function key(secret: string) {
  return crypto.subtle.importKey(
    "raw",
    bytes(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}
export function fail(status: number, message: string, code = "REQUEST_FAILED") {
  return Response.json(
    { error: { code, message } },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}
export function sameOrigin(request: Request) {
  return request.headers.get("origin") === new URL(request.url).origin;
}
export async function session(request: Request, env: AccessEnv) {
  if (!env.SESSION_SECRET || env.SESSION_SECRET.length < 32) return false;
  const token = request.headers
    .get("cookie")
    ?.split(";")
    .map((v) => v.trim())
    .find((v) => v.startsWith(`${COOKIE}=`))
    ?.slice(COOKIE.length + 1);
  if (!token || token.length > 300) return false;
  const [user, exp, nonce, signature, extra] = token.split(".");
  const expiry = Number(exp),
    now = Math.floor(Date.now() / 1000);
  if (
    user !== "admin" ||
    !nonce ||
    !signature ||
    extra ||
    !Number.isInteger(expiry) ||
    expiry <= now ||
    expiry > now + 43200
  )
    return false;
  try {
    return await crypto.subtle.verify(
      "HMAC",
      await key(env.SESSION_SECRET),
      decode(signature),
      bytes(`${user}.${exp}.${nonce}`),
    );
  } catch {
    return false;
  }
}
export async function passwordHash(
  password: string,
  salt = encode(crypto.getRandomValues(new Uint8Array(16))),
) {
  const material = await crypto.subtle.importKey(
    "raw",
    bytes(password),
    "PBKDF2",
    false,
    ["deriveBits"],
  );
  const hash = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt: bytes(salt), iterations: 100000, hash: "SHA-256" },
    material,
    256,
  );
  return `pbkdf2:${salt}:${encode(hash)}`;
}
async function matchesPassword(password: string, encoded: string) {
  const [scheme, salt, hash, extra] = encoded.split(":");
  if (!salt || !hash || extra) return false;
  if (scheme === "pbkdf2") {
    const computed = (await passwordHash(password, salt)).split(":")[2]!;
    const actual = bytes(computed),
      expected = bytes(hash);
    return (
      actual.length === expected.length && timingSafeEqual(actual, expected)
    );
  }
  if (scheme !== "scrypt") return false;
  const expected = decode(hash);
  if (expected.length !== 64) return false;
  const actual = await new Promise<Buffer>((resolve, reject) => {
    scrypt(password, salt, 64, (error, derived) =>
      error ? reject(error) : resolve(derived),
    );
  });
  return timingSafeEqual(actual, expected);
}
export async function login(request: Request, env: AccessEnv) {
  if (!sameOrigin(request)) return fail(403, "Request not allowed.");
  if (
    !env.ADMIN_PASSWORD_HASH ||
    !env.SESSION_SECRET ||
    env.SESSION_SECRET.length < 32
  )
    return fail(503, "Login is not configured.");
  if (
    env.LOGIN_LIMITER &&
    !(
      await env.LOGIN_LIMITER.limit({
        key: request.headers.get("cf-connecting-ip") ?? "local",
      })
    ).success
  )
    return fail(429, "Too many attempts. Try again in a minute.");
  try {
    const raw = await request.text();
    if (raw.length > 4096) return fail(413, "Request too large.");
    const body = JSON.parse(raw);
    if (
      typeof body.username !== "string" ||
      typeof body.password !== "string" ||
      body.password.length > 256
    )
      return fail(400, "Enter your username and password.");
    if (
      body.username !== "admin" ||
      !(await matchesPassword(body.password, env.ADMIN_PASSWORD_HASH))
    )
      return fail(401, "Username or password is incorrect.");
    const payload = `admin.${Math.floor(Date.now() / 1000) + 43200}.${encode(crypto.getRandomValues(new Uint8Array(16)))}`;
    const token = `${payload}.${encode(await crypto.subtle.sign("HMAC", await key(env.SESSION_SECRET), bytes(payload)))}`;
    return Response.json(
      { ok: true },
      {
        headers: {
          "Set-Cookie": `${COOKIE}=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200${new URL(request.url).protocol === "https:" ? "; Secure" : ""}`,
          "Cache-Control": "no-store",
        },
      },
    );
  } catch {
    return fail(400, "Enter your username and password.");
  }
}
export function logout(request: Request) {
  if (!sameOrigin(request)) return fail(403, "Request not allowed.");
  return Response.json(
    { ok: true },
    {
      headers: {
        "Set-Cookie": `${COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0${new URL(request.url).protocol === "https:" ? "; Secure" : ""}`,
      },
    },
  );
}
import { scrypt, timingSafeEqual } from "node:crypto";
