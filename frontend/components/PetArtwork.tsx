import type { SVGProps } from "react";

/** Original CSS-independent pet-retail motifs; no network images or assets required. */
export function PawMark(props: SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 48 48" fill="currentColor" aria-hidden="true" {...props}>
    <ellipse cx="9" cy="18" rx="5" ry="7" transform="rotate(-22 9 18)"/>
    <ellipse cx="20" cy="10" rx="5" ry="7" transform="rotate(-8 20 10)"/>
    <ellipse cx="32" cy="12" rx="5" ry="7" transform="rotate(16 32 12)"/>
    <ellipse cx="42" cy="22" rx="4.5" ry="6.5" transform="rotate(25 42 22)"/>
    <path d="M10 34c1.1-5 5.2-7.4 8.1-11.1 2.1-2.7 4.5-4.4 7.3-4.4 3.2 0 5.3 2.3 7.6 5.4 2.6 3.5 6.8 5.5 6.4 10.6-.4 5.5-4.2 8.4-9.2 7.2-3.7-.9-4.7-2.9-7.8-2.9-3.6 0-6 2.3-9.5 3.1C12.7 42.9 8.6 39.2 10 34Z"/>
  </svg>;
}

export function PetArtwork() {
  return <div className="pet-artwork" aria-hidden="true">
    <div className="art-promo-sticker">MADE FOR<br/><b>HAPPY TAILS</b><span>✳</span></div>
    <svg viewBox="0 0 520 410" className="pet-artwork-svg" role="presentation">
      <defs>
        <pattern id="pawDots" width="35" height="35" patternUnits="userSpaceOnUse"><circle cx="4" cy="4" r="2" fill="#314440" opacity=".12"/></pattern>
        <filter id="softLift" x="-40%" y="-40%" width="180%" height="200%"><feDropShadow dx="2" dy="13" stdDeviation="13" floodColor="#312e29" floodOpacity=".14"/></filter>
      </defs>
      <ellipse cx="257" cy="353" rx="216" ry="24" fill="#3a3a31" opacity=".10"/>
      <circle cx="284" cy="170" r="161" fill="#f9bd83" opacity=".47"/>
      <circle cx="284" cy="170" r="141" fill="none" stroke="#e79160" strokeWidth="2" strokeDasharray="4 10" opacity=".65"/>
      <path d="M90 248C25 202 45 119 92 108c35-8 70 21 73 59 5 47-29 81-75 81Z" fill="#eaa56d"/>
      <path d="M92 218c-22 10-35 30-27 46 9 17 34 14 48-3" stroke="#503d31" strokeWidth="10" strokeLinecap="round" fill="none"/>
      <path d="M74 110c-20-23-43-16-38 6 3 18 11 31 23 43M134 108c21-24 45-17 42 6-2 18-13 33-25 44" fill="#7a5039"/>
      <path d="M85 126c-33 12-40 39-32 64 6 20 25 37 50 44 24 6 55-9 65-29 13-26 0-61-24-75-22-13-43-14-59-4Z" fill="#b77a50"/>
      <path d="M94 170c-7-9-24-11-31 1 0 24 15 35 37 35 22 0 35-11 41-28-8-19-34-18-47-8Z" fill="#f7d6b7"/>
      <path d="M87 162c4-6 11-6 16 0m24 0c4-6 11-6 15 0" stroke="#41382f" strokeWidth="4" strokeLinecap="round" fill="none"/>
      <path d="M101 183q11-12 22 0-1 10-11 10t-11-10Z" fill="#43352f"/>
      <path d="M111 194v9m-12-1q13 9 26 0" stroke="#4f3831" strokeWidth="3" fill="none" strokeLinecap="round"/>
      <path d="M83 240q-34 32-23 98h93q7-62-22-99" fill="#b77a50"/>
      <path d="M90 246q-13 50 10 79h34q19-37 2-79" fill="#dfad80"/>
      <path d="M59 338h34m33 0h32" stroke="#6e4a3b" strokeWidth="12" strokeLinecap="round"/>
      <path d="M65 241c-19 9-32 4-34-14" stroke="#b77a50" strokeWidth="17" strokeLinecap="round" fill="none"/>
      <path d="M370 149 347 94l-36 40c-28 0-43 19-42 45-5 28 11 47 40 55 19 6 46-1 58-16 13-16 11-42 3-69Z" fill="#ece6d6"/>
      <path d="m279 143-8-44 40 35m59 15-6-51-36 40" stroke="#ede6d6" strokeWidth="13" strokeLinejoin="round" fill="#ede6d6"/>
      <path d="m288 134-5-23 19 21m53 1 2-21-20 17" fill="#f4b1a5"/>
      <path d="M303 172h1m43 0h1" stroke="#3d4940" strokeWidth="9" strokeLinecap="round"/>
      <path d="m320 189 10 0-5 8Z" fill="#c98778"/>
      <path d="M325 197v8m-11-1q11 8 22 0" stroke="#666153" strokeWidth="2.7" strokeLinecap="round" fill="none"/>
      <path d="M297 188l-24-6m24 13-24 2m81-9 24-5m-25 13 26 2" stroke="#9e9b88" strokeWidth="2" strokeLinecap="round"/>
      <path d="M291 234c-24 28-23 70-11 104h81c13-38 10-77-11-103" fill="#e7dec9"/>
      <path d="M306 238q-7 30 3 60m32-59q9 35-2 59" stroke="#f9f6ef" strokeWidth="7" strokeLinecap="round"/>
      <path d="M286 336h24m30 0h27" stroke="#aca997" strokeWidth="11" strokeLinecap="round"/>
      <g filter="url(#softLift)" transform="translate(179 112) rotate(-4)">
        <path d="M7 12h111l10 208H0Z" fill="#f6eee0" stroke="#4f5648" strokeWidth="4"/>
        <path d="M7 12h111l-7 37H12Z" fill="#e99570"/>
        <path d="M20 64h88v104H20Z" rx="5" fill="#324d44"/>
        <path d="M20 174h88v28H20Z" fill="#ebba77"/>
        <g transform="translate(47 76) scale(.65)" fill="#f4c68e"><ellipse cx="9" cy="18" rx="5" ry="7"/><ellipse cx="20" cy="10" rx="5" ry="7"/><ellipse cx="32" cy="12" rx="5" ry="7"/><ellipse cx="42" cy="22" rx="5" ry="7"/><path d="M10 34c2-7 8-15 15-15 8 0 12 10 15 16 2 8-7 11-15 7-8-4-18 1-15-8Z"/></g>
        <text x="64" y="134" textAnchor="middle" fill="#fff4db" fontSize="16" fontWeight="800" letterSpacing="1">PET FOOD</text>
        <text x="64" y="154" textAnchor="middle" fill="#f9cf9d" fontSize="8" fontWeight="700" letterSpacing="2">NOURISH + PLAY</text>
        <text x="64" y="193" textAnchor="middle" fill="#365249" fontSize="10" fontWeight="800" letterSpacing="1">15 LB</text>
      </g>
      <path d="M417 280q25-9 39 10m-9-23q26-10 38 15" fill="none" stroke="#e99b67" strokeWidth="6" strokeLinecap="round" opacity=".75"/>
      <g transform="translate(415 205) rotate(13)"><ellipse cx="0" cy="0" rx="8" ry="11" fill="#f5a775"/><ellipse cx="20" cy="-7" rx="7" ry="10" fill="#f5a775"/><ellipse cx="39" cy="0" rx="7" ry="10" fill="#f5a775"/><path d="M-6 26Q15 0 29 20q24 22 7 30Q24 55 15 44-17 56-6 26Z" fill="#f5a775"/></g>
    </svg>
    <div className="art-note art-note-top"><span className="art-note-dot"/> STOCK IN MOTION</div>
    <div className="art-note art-note-bottom">GOOD DATA. <b>GOOD DOGS.</b></div>
  </div>;
}
