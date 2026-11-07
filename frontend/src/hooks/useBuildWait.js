import { useCallback, useRef } from 'react';
import { api } from '../api';
import { waitForBuildIdle } from '../build-wait';

const sleep = (ms) => new Promise(resolve => setTimeout(resolve, ms));

/**
 * The post-mutation dance App's update / duplicate / rename / delete
 * handlers each spelled out by hand: wait (up to 60 s) for the server's
 * rebuild to finish, point the preview at the fresh font, wait for the
 * browser to actually have it, then let the DOM settle.
 *
 * Returns a stable async function; `vfFamilyId` is read through a ref so
 * the function never goes stale while a rebuild is in flight.
 */
export function useBuildWait({ vfFamilyId, setFontUrl, setFontLoaded }) {
  const familyRef = useRef(vfFamilyId);
  familyRef.current = vfFamilyId;

  // Font readiness: document.fonts.check() turns true once the FontFace
  // registered for the preview family has loaded.
  const waitForFontReady = useCallback(async (maxAttempts = 50, delayMs = 100) => {
    const family = familyRef.current;
    if (!family) {
      // No family registered yet; the polling loop will populate it.
      return false;
    }
    for (let i = 0; i < maxAttempts; i++) {
      if (document.fonts && document.fonts.check(`12px "${family}"`)) {
        // Also wait for fonts.ready to ensure font is fully ready
        await document.fonts.ready;
        return true;
      }
      await sleep(delayMs);
    }
    return false; // Timeout - font might not be ready, but proceed anyway
  }, []);

  return useCallback(async () => {
    await waitForBuildIdle();
    // Reload font URL to get updated font (with timestamp to force reload)
    setFontUrl(api.getFontUrl());
    setFontLoaded(false);
    await sleep(500);
    setFontLoaded(true);
    // Wait for font to be loaded before touching the DOM
    await waitForFontReady();
    // Additional small delay to ensure DOM is updated after font load
    await sleep(200);
  }, [setFontUrl, setFontLoaded, waitForFontReady]);
}

/** Bring an instance row into view once the DOM has settled. */
export function scrollToInstance(name) {
  const element = document.querySelector(`[data-instance-name="${name}"]`);
  if (element) {
    // Use requestAnimationFrame to ensure DOM is ready
    requestAnimationFrame(() => {
      element.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  }
}
