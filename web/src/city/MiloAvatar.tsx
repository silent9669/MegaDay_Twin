export function MiloAvatar({
  state = "idle",
}: {
  state?: "idle" | "greeting" | "thinking" | "notification" | "error";
}) {
  return (
    <span className={`milo-sprite milo-${state}`} aria-hidden="true">
      <img className="milo-still" src={`/assets/milo/${state}.png`} alt="" />
      {state !== "idle" && (
        <img
          className="milo-motion"
          src={`/assets/milo/${state}-sheet.png`}
          alt=""
        />
      )}
    </span>
  );
}
