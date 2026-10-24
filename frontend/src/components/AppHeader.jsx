import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Icon } from "./Icon.jsx";

function useDismiss(open, setOpen) {
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return;
    const down = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    const key = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [open]);
  return ref;
}

function Menu({ button, items, className = "", buttonClass = "ck-menu__btn" }) {
  const [open, setOpen] = useState(false);
  const ref = useDismiss(open, setOpen);
  return (
    <div className={"ck-menu " + className} ref={ref}>
      <button type="button" className={buttonClass} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {button}
        <Icon name="chevron-down" />
      </button>
      {open && (
        <ul className="ck-menu__list" role="menu">
          {items.map((it) => (
            <li key={it.label} role="none">
              <button type="button" role="menuitem" className="ck-menu__item" onClick={() => { setOpen(false); it.onClick && it.onClick(); }}>
                {it.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Шапка кабинета. Название компании — текстом, логотипа нет.
 * Если у пользователя несколько компаний, название становится выбором компании.
 */
export function AppHeader({ title, homeTo = "/", links = [], userName, menuItems = [], companies }) {
  return (
    <header className="ck-header">
      <div className="ck-header__inner">
        {companies && companies.length > 1 ? (
          <Menu
            className="ck-menu--left"
            buttonClass="ck-header__title ck-header__title--select"
            button={title}
            items={companies}
          />
        ) : (
          <Link className="ck-header__title" to={homeTo}>{title}</Link>
        )}
        <nav className="ck-header__nav" aria-label="Основная навигация">
          {links.map((l) => (
            <Link key={l.label} className="ck-header__link" to={l.to} aria-current={l.current ? "page" : undefined}>{l.label}</Link>
          ))}
          {userName && <Menu button={userName} items={menuItems} />}
        </nav>
      </div>
    </header>
  );
}
