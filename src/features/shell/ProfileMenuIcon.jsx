/* Filled silhouettes used only in the account dropdown. */
export function ProfileMenuIcon({ name }) {
  const shapes = {
    model: <>
      <circle cx="12" cy="7" r="4.5" />
      <path d="M3.5 20c0-4.1 3.8-7 8.5-7s8.5 2.9 8.5 7v.5a1 1 0 0 1-1 1h-15a1 1 0 0 1-1-1Z" />
    </>,
    pricing: <>
      <rect className="profile-menu-icon-card-back" x="5" y="3" width="17" height="13" rx="2.5" />
      <rect className="profile-menu-icon-card-front" x="2" y="7" width="18" height="14" rx="2.5" />
      <path className="profile-menu-icon-card-mark" d="M3 11.5h16M5.5 16.5h4" />
    </>,
    subscription: <>
      <rect x="3" y="5" width="18" height="17" rx="3" />
      <rect x="6" y="2" width="3" height="7" rx="1.5" />
      <rect x="15" y="2" width="3" height="7" rx="1.5" />
      <rect className="profile-menu-icon-detail" x="6" y="10" width="12" height="2" rx="1" />
      <path className="profile-menu-icon-line" d="m8 16 2.5 2.5L16 14" />
    </>,
    credits: <>
      <path d="M10 6c0-2.2 2.7-4 6-4s6 1.8 6 4v11c0 2.2-2.7 4-6 4s-6-1.8-6-4Z" />
      <path className="profile-menu-icon-line" d="M12 7c2.2 1.3 5.8 1.3 8 0M12 12c2.2 1.3 5.8 1.3 8 0" />
      <path d="M2 13c0-2.2 2.7-4 6-4s6 1.8 6 4v6c0 2.2-2.7 4-6 4s-6-1.8-6-4Z" />
      <path className="profile-menu-icon-line" d="M4 13c2.2 1.3 5.8 1.3 8 0M4 18c2.2 1.3 5.8 1.3 8 0" />
    </>,
    logout: <>
      <path d="M5 2.5h8a2 2 0 0 1 2 2v17H5a2 2 0 0 1-2-2v-15a2 2 0 0 1 2-2Z" />
      <path className="profile-menu-icon-detail" d="M6.5 5.5h5v16h-5zM18.1 7.5a1 1 0 0 1 1.4 0l3.8 3.8a1 1 0 0 1 0 1.4l-3.8 3.8a1 1 0 0 1-1.4-1.4l2.1-2.1H14a1 1 0 0 1 0-2h6.2l-2.1-2.1a1 1 0 0 1 0-1.4Z" />
    </>,
  };

  return <svg className={`profile-menu-icon profile-menu-icon--${name}`} viewBox="0 0 24 24" aria-hidden="true" focusable="false">{shapes[name]}</svg>;
}
