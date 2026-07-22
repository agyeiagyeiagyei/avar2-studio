import React, { useEffect, useRef, useState } from 'react';
import './DemoLanding.css';
import howItWorksGif from '../assets/demo/how-it-works.gif';
import secondaryAxesGif from '../assets/demo/secondary-axes.gif';
import customAxesGif from '../assets/demo/custom-axes.gif';
import avar2SizeSvg from '../assets/demo/avar2-size.svg';

/**
 * Landing overlay for the hosted shared-demo instance (health.demo).
 * Full-height scroll-snap sections: a hero set in the loaded demo
 * font, then three screen-recorded flows (text left, GIF right).
 * Dismiss lands the visitor in the real app; sessionStorage keeps it
 * dismissed for the visit.
 */

const SECTIONS = [
  { id: 'hero', label: 'avar2-studio' },
  { id: 'how', label: 'How it works' },
  { id: 'secondary', label: 'Set up secondary axes' },
  { id: 'custom', label: 'Define custom axes' },
  { id: 'avar2', label: 'Why avar2' },
];

const FEATURES = [
  {
    id: 'how',
    title: 'How it works',
    gif: howItWorksGif,
    alt: 'Dragging parametric sliders updates instance rows live, then the Preview tab drives the built font',
    bullets: [
      'Named instances render as live rows of the real compiled font.',
      'Drag parametric sliders — every row updates as you move.',
      'The Preview tab drives all axes of the built font, avar2 applied.',
    ],
  },
  {
    id: 'secondary',
    title: 'Set up secondary axes',
    gif: secondaryAxesGif,
    alt: 'Declaring a glyph-scoped axis, picking glyphs, and seeding brace layers',
    bullets: [
      'Declare a glyph-scoped axis — a crossbar, a figure tweak, anything.',
      'Pick the glyphs it changes and the value it pins.',
      'Layers are seeded per master corner, ready to draw in the embedded Fontra editor.',
    ],
  },
  {
    id: 'custom',
    title: 'Define custom axes',
    gif: customAxesGif,
    alt: 'Adding a custom user-facing axis and mapping an instance to it with a slider',
    bullets: [
      'Add any user-facing axis — weight, width, grade, or your own.',
      'Map instances onto it with sliders, no numbers to hand-edit.',
      'The avar2 table is rebuilt around your mapping.',
    ],
  },
  {
    id: 'avar2',
    title: 'Why avar2',
    gif: avar2SizeSvg,
    alt: 'Bar chart: the same designspace is 52 KB compiled with avar2 versus 324 KB with mappings flattened into deltas',
    intro: 'avar2 is the second-generation axis mapping: the font keeps its '
      + 'compact parametric masters plus one small table that re-projects '
      + 'user coordinates onto them at render time.',
    bullets: [
      'One entry per authored style — user location in, parametric location out.',
      'The traditional route bakes every mapping into outline deltas: delta regions multiply and gvar balloons.',
      'Identical rendering, 6× smaller.',
    ],
  },
];

function DemoLanding({ familyId, fontLoaded, onEnter }) {
  const scrollRef = useRef(null);
  const [active, setActive] = useState('hero');

  useEffect(() => {
    const root = scrollRef.current;
    if (!root) return undefined;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) setActive(e.target.dataset.section);
        }
      },
      { root, threshold: 0.55 }
    );
    root.querySelectorAll('[data-section]').forEach((s) => observer.observe(s));
    return () => observer.disconnect();
  }, []);

  const jump = (id) => {
    const el = scrollRef.current?.querySelector(`[data-section="${id}"]`);
    if (el) el.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="demo-landing">
      <div className="dl-topbar">
        <span className="dl-brand">avar2-studio</span>
        <div>
          <a
            className="dl-gh"
            href="https://github.com/agyeiagyeiagyei/avar2-studio"
            target="_blank"
            rel="noreferrer"
          >
            GitHub
          </a>
          <button className="dl-enter" onClick={onEnter}>Enter the studio →</button>
        </div>
      </div>

      <div className="dl-scroll" ref={scrollRef}>
        <section className="dl-section dl-hero" data-section="hero">
          <div className="dl-hero-inner">
            <div
              className="dl-specimen"
              style={{
                fontFamily: fontLoaded && familyId ? `"${familyId}", sans-serif` : 'sans-serif',
                fontVariationSettings: '"opsz" 144, "wdth" 160, "wght" 700',
              }}
            >
              avar2 studio
            </div>
            <h1>Visual authoring and preview for avar2 variable fonts.</h1>
            <p>
              Point it at a <code>.glyphs</code> or <code>.designspace</code> file
              whose masters have interpolable glyphs, and author the mapping from
              familiar axes onto the parametric axes that shape the strokes.
              Every preview is the actual compiled font.
            </p>
            <button className="dl-enter dl-enter-big" onClick={onEnter}>
              Enter the studio →
            </button>
          </div>
        </section>

        {FEATURES.map((f) => (
          <section className="dl-section dl-feature" data-section={f.id} key={f.id}>
            <div className="dl-feature-grid">
              <div className="dl-feature-text">
                <h2>{f.title}</h2>
                {f.intro && <p className="dl-feature-intro">{f.intro}</p>}
                <ul>
                  {f.bullets.map((t) => <li key={t}>{t}</li>)}
                </ul>
              </div>
              <img src={f.gif} alt={f.alt} loading="lazy" />
            </div>
          </section>
        ))}
      </div>

      <nav className="dl-dots" aria-label="Sections">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            className={`dl-dot ${active === s.id ? 'on' : ''}`}
            title={s.label}
            aria-label={s.label}
            onClick={() => jump(s.id)}
          />
        ))}
      </nav>
    </div>
  );
}

export default DemoLanding;
