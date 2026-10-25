import { ButtonLink, Card, EmptyState, Skeleton } from "../components/index.js";

export function NotFoundPage({ home = "/" }) {
  return (
    <main className="page">
      <Card><EmptyState title="Такой страницы нет" action={<ButtonLink variant="primary" to={home}>На главную</ButtonLink>} /></Card>
    </main>
  );
}

export function LoadError({ error, home = "/" }) {
  const title = error?.status === 403 ? error.message || "Эта заявка принадлежит другой компании" : error?.status === 404 ? "Такой страницы нет" : error?.status === 0 ? "Нет связи с сервером" : error?.message || "Не удалось загрузить страницу";
  return (
    <main className="page">
      <Card><EmptyState title={title} action={error?.status === 404 || error?.status === 403 ? <ButtonLink variant="primary" to={home}>На главную</ButtonLink> : null} /></Card>
    </main>
  );
}

export function PageSkeleton() {
  return (
    <main className="page" aria-busy="true" aria-label="Загрузка">
      <Skeleton variant="text" lines={1} width="40%" height={34} />
      <Card><div className="stack stack-6"><Skeleton variant="control" /><Skeleton variant="text" lines={1} width="50%" /></div></Card>
      <div className="grid grid--aside">
        <Card><div className="stack stack-6"><Skeleton variant="text" lines={1} width="70%" height={40} /><Skeleton variant="control" /><Skeleton lines={3} /></div></Card>
        <Card><Skeleton lines={5} /></Card>
      </div>
    </main>
  );
}
