export function BrandMark({ size = 32 }: { size?: number }) {
  return (
    <img
      className="city-brand-mark"
      src="/icon.svg?v=2"
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
    />
  );
}
export function Brand() {
  return (
    <>
      <BrandMark />
      <span>
        MegaDay <b>Twin</b>
      </span>
    </>
  );
}
