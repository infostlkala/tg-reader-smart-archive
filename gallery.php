<?php
/**
 * گالری محصولات v2 — نمایش همه رسانه‌ها (عکس/گیف/ویدئو/فایل) از tg_messages
 * آدرس: https://stlkala.com/gallery.php
 */
header('Content-Type: text/html; charset=utf-8');

$DB_HOST = 'localhost';
$DB_NAME = 'elecset3_tgarchive';
$DB_USER = 'elecset3_tguser';
$DB_PASS = 'Tglo0qHPOrMVX-6OgzQvI!7x';

try {
    $pdo = new PDO(
        "mysql:host=$DB_HOST;dbname=$DB_NAME;charset=utf8mb4",
        $DB_USER, $DB_PASS,
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
         PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
    );
} catch (PDOException $e) {
    http_response_code(500);
    exit('<h1 style="font-family:sans-serif">خطای اتصال دیتابیس</h1>');
}

$rows = $pdo->query(
    "SELECT id, channel, date, title, description, category, tags,
            has_media, media_name, media_path, media_url, media_mime,
            product_folder, link
     FROM tg_messages
     ORDER BY CAST(id AS SIGNED) DESC"
)->fetchAll();

$products = [];
$ungrouped = [];
foreach ($rows as $m) {
    if (!empty($m['product_folder']) && !empty($m['media_url'])) {
        $products[$m['product_folder']][] = $m;
    } elseif (!empty($m['media_url'])) {
        $ungrouped[] = $m;
    }
}
uasort($products, function ($a, $b) {
    return max(array_column($b, 'id')) <=> max(array_column($a, 'id'));
});

function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

function is_gif(array $m): bool {
    $n = strtolower((string)$m['media_name']);
    return substr($n, -4) === '.gif' || (string)$m['media_mime'] === 'image/gif';
}

function is_video(array $m): bool {
    return $m['has_media'] === 'video' || substr((string)$m['media_mime'], 0, 6) === 'video/';
}

function is_visual(array $m): bool {
    if (empty($m['media_url'])) return false;
    $n = strtolower((string)$m['media_name']);
    if (preg_match('/\.(zip|rar|7z|stl|3mf|obj|step|stp|gcode)$/i', $n)) return false;
    return in_array($m['has_media'], ['photo', 'video'], true) || is_gif($m);
}

function product_title(array $members): string {
    $bad = ['تصویر تلگرام', 'فایل تلگرام', 'ویدئو تلگرام', 'تصویر بدون توضیح',
            'سند نامشخص', 'بدون عنوان'];
    foreach ($members as $m) {
        $t = trim((string)$m['title']);
        if ($t && !in_array($t, $bad, true) && !preg_match('/^https?:/', $t)
            && !preg_match('/\.(jpe?g|png|zip|mp4|stl|3mf|rar|7z)$/i', $t)) {
            return $t;
        }
    }
    foreach ($members as $m) {
        $t = trim((string)$m['text']);
        if (mb_strlen($t) > 10) return mb_substr($t, 0, 60);
    }
    return (string)$members[0]['product_folder'];
}

function product_file(array $members): ?array {
    foreach ($members as $m) {
        $n = (string)$m['media_name'];
        if ($n && preg_match('/\.(zip|rar|7z|stl|3mf|obj|step|stp|gcode)$/i', $n)) return $m;
    }
    return null;
}

function product_description(array $members): string {
    foreach ($members as $m) {
        $d = trim((string)$m['description']);
        if ($d && mb_strlen($d) > 15) return mb_substr($d, 0, 200);
    }
    return '';
}
?>
<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>گالری محصولات — 3D مدل‌ها</title>
<style>
  :root { --bg:#0f1420; --card:#171f2e; --card2:#1c2537; --txt:#e8edf5;
    --muted:#8fa0b8; --accent:#4f8cff; --accent2:#7b5cff; --radius:16px; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--txt); font-family:Vazirmatn,Tahoma,sans-serif;
    line-height:1.7; padding-bottom:60px; }
  .hero { padding:48px 20px 30px; text-align:center;
    background:linear-gradient(135deg,#16203a 0%,#101625 60%,#131a2b 100%);
    border-bottom:1px solid #232f47; }
  .hero h1 { font-size:28px; background:linear-gradient(90deg,var(--accent),var(--accent2));
    -webkit-background-clip:text; background-clip:text; color:transparent; }
  .hero p { color:var(--muted); margin-top:8px; font-size:14px; }
  .stats { display:flex; gap:14px; justify-content:center; margin-top:18px; flex-wrap:wrap; }
  .stat { background:var(--card); border:1px solid #26324c; border-radius:12px;
    padding:10px 22px; font-size:13px; }
  .stat b { color:var(--accent); font-size:17px; margin-inline-start:6px; }
  .wrap { max-width:1200px; margin:0 auto; padding:0 20px; }
  h2.sec { margin:36px 0 18px; font-size:20px; display:flex; align-items:center; gap:10px; }
  h2.sec::before { content:''; width:4px; height:22px; border-radius:4px;
    background:linear-gradient(180deg,var(--accent),var(--accent2)); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); gap:22px; }
  .card { background:var(--card); border:1px solid #26324c; border-radius:var(--radius);
    overflow:hidden; transition:transform .2s,border-color .2s,box-shadow .2s;
    display:flex; flex-direction:column; }
  .card:hover { transform:translateY(-4px); border-color:var(--accent);
    box-shadow:0 12px 30px rgba(79,140,255,.15); }
  .main-media { position:relative; aspect-ratio:4/3; background:var(--card2); overflow:hidden; }
  .main-media img, .main-media video { width:100%; height:100%; object-fit:cover; display:block; }
  .main-media .placeholder { width:100%; height:100%; display:flex; align-items:center;
    justify-content:center; font-size:44px; color:#3a4a6b; }
  .badge-count { position:absolute; top:10px; inset-inline-start:10px; z-index:2;
    background:rgba(15,20,32,.85); backdrop-filter:blur(4px); border-radius:999px;
    padding:3px 12px; font-size:12px; }
  .thumbs { display:flex; gap:6px; padding:8px 10px 0; overflow-x:auto; }
  .thumbs .th { width:64px; height:48px; flex-shrink:0; border-radius:8px; overflow:hidden;
    cursor:pointer; border:2px solid transparent; background:var(--card2); position:relative; }
  .thumbs .th img, .thumbs .th video { width:100%; height:100%; object-fit:cover; display:block; }
  .thumbs .th.active { border-color:var(--accent); }
  .thumbs .th .vid-ic { position:absolute; inset:0; display:flex; align-items:center;
    justify-content:center; font-size:18px; background:rgba(0,0,0,.3); }
  .card .body { padding:14px 16px 16px; display:flex; flex-direction:column; gap:8px; flex:1; }
  .card h3 { font-size:15px; line-height:1.5; }
  .card .desc { color:var(--muted); font-size:12.5px; }
  .meta { color:var(--muted); font-size:12px; display:flex; gap:10px; flex-wrap:wrap; }
  .tags { display:flex; flex-wrap:wrap; gap:6px; margin-top:auto; }
  .tag { background:#202c44; color:#9db4d8; border-radius:999px; padding:2px 10px; font-size:11px; }
  .actions { display:flex; gap:8px; margin-top:10px; flex-wrap:wrap; }
  .btn { display:inline-flex; align-items:center; gap:6px; text-decoration:none;
    border-radius:10px; padding:8px 14px; font-size:13px; font-weight:600; transition:filter .15s; }
  .btn:hover { filter:brightness(1.15); }
  .btn-dl { background:linear-gradient(90deg,var(--accent),var(--accent2)); color:#fff; }
  .btn-tg { background:#22304d; color:#a9c0e8; }
  .msg-item { background:var(--card); border:1px solid #26324c; border-radius:14px;
    padding:12px; display:flex; flex-direction:column; gap:8px; }
  .msg-item .media-box { border-radius:10px; overflow:hidden; background:var(--card2); }
  .msg-item .media-box img, .msg-item .media-box video { width:100%; max-height:260px;
    object-fit:cover; display:block; }
  .msg-item h4 { font-size:13.5px; font-weight:600; padding:0 4px; }
  .msg-item .meta { padding:0 4px 4px; }
  footer { text-align:center; color:var(--muted); font-size:12px; margin-top:50px; }
  @media (max-width:640px) { .hero h1 { font-size:22px; } }
</style>
</head>
<body>

<div class="hero">
  <h1>🖨️ گالری مدل‌های سه‌بعدی</h1>
  <p>آرشیو خودکار محصولات کانال تلگرام — عنوان، توضیح و دسته‌بندی با هوش مصنوعی</p>
  <div class="stats">
    <div class="stat">📦 محصولات <b><?= count($products) ?></b></div>
    <div class="stat">💬 کل پست‌ها <b><?= count($rows) ?></b></div>
  </div>
</div>

<div class="wrap">

<?php if ($products): ?>
  <h2 class="sec">📦 محصولات (گروه‌بندی هوشمند)</h2>
  <div class="grid">
  <?php foreach ($products as $folder => $members):
      $title = product_title($members);
      $desc  = product_description($members);
      $file  = product_file($members);
      $visuals = array_values(array_filter($members, 'is_visual'));
      $cover = null; $coverIdx = 0;
      foreach ($visuals as $i => $m) {
          if ($m['has_media'] === 'photo' && !is_gif($m)) { $cover = $m; $coverIdx = $i; break; }
      }
      if ($cover === null && $visuals) { $cover = $visuals[0]; $coverIdx = 0; }
      $tags = [];
      foreach ($members as $m) {
          $t = json_decode((string)$m['tags'], true) ?: [];
          foreach ($t as $tg) $tags[$tg] = true;
      }
      $tags = array_slice(array_keys($tags), 0, 4);
      $newest = max(array_column($members, 'id'));
      $chan = $members[0]['channel'];
      $hash = md5($folder);
  ?>
    <div class="card">
      <div class="main-media" id="main-<?= $hash ?>">
        <?php if ($cover): ?>
          <?php if (is_video($cover)): ?>
            <video src="<?= h($cover['media_url']) ?>" controls preload="metadata"></video>
          <?php else: ?>
            <img src="<?= h($cover['media_url']) ?>" alt="<?= h($title) ?>" loading="lazy">
          <?php endif; ?>
        <?php else: ?>
          <div class="placeholder">📦</div>
        <?php endif; ?>
        <span class="badge-count"><?= count($visuals) ?> رسانه</span>
      </div>
      <?php if (count($visuals) > 1): ?>
      <div class="thumbs">
        <?php foreach ($visuals as $i => $m): ?>
          <div class="th <?= $i === $coverIdx ? 'active' : '' ?>"
               onclick="swapMedia('<?= $hash ?>', <?= $i ?>, this)">
            <?php if (is_video($m)): ?>
              <video src="<?= h($m['media_url']) ?>#t=0.5" preload="metadata" muted></video>
              <span class="vid-ic">▶</span>
            <?php else: ?>
              <img src="<?= h($m['media_url']) ?>" loading="lazy" alt="">
            <?php endif; ?>
          </div>
        <?php endforeach; ?>
      </div>
      <?php endif; ?>
      <div class="body">
        <h3><?= h($title) ?></h3>
        <?php if ($desc): ?><div class="desc"><?= h($desc) ?></div><?php endif; ?>
        <div class="meta">
          <span>#<?= h($newest) ?></span>
          <?php if ($file): ?><span>📎 <?= h(mb_substr((string)$file['media_name'] ?: 'فایل محصول', 0, 34)) ?></span><?php endif; ?>
        </div>
        <?php if ($tags): ?>
        <div class="tags"><?php foreach ($tags as $tg): ?><span class="tag"><?= h($tg) ?></span><?php endforeach; ?></div>
        <?php endif; ?>
        <div class="actions">
          <?php if ($file && $file['media_url']): ?>
            <a class="btn btn-dl" href="<?= h($file['media_url']) ?>?download=1" download>⬇ دانلود فایل</a>
          <?php endif; ?>
          <a class="btn btn-tg" href="https://t.me/<?= h($chan) ?>/<?= h($newest) ?>" target="_blank">تلگرام ↗</a>
        </div>
      </div>
    </div>
  <?php endforeach; ?>
  </div>
<?php endif; ?>

<?php if ($ungrouped): ?>
  <h2 class="sec">🗂 سایر رسانه‌ها</h2>
  <div class="grid">
  <?php foreach (array_slice($ungrouped, 0, 36) as $m): ?>
    <div class="msg-item">
      <div class="media-box">
        <?php if (is_video($m)): ?>
          <video src="<?= h($m['media_url']) ?>" controls preload="metadata"></video>
        <?php else: ?>
          <a href="<?= h($m['media_url']) ?>" target="_blank">
            <img src="<?= h($m['media_url']) ?>" loading="lazy" alt="">
          </a>
        <?php endif; ?>
      </div>
      <h4><?= h(mb_substr(trim((string)$m['title'] ?: (string)$m['media_name'] ?: 'بدون عنوان'), 0, 60)) ?></h4>
      <div class="meta">
        <span>#<?= h($m['id']) ?></span>
        <a href="<?= h($m['media_url']) ?>?download=1" style="color:var(--accent)">دانلود</a>
      </div>
    </div>
  <?php endforeach; ?>
  </div>
<?php endif; ?>

<footer>داده‌ها از آرشیو خودکار کانال تلگرام — آخرین به‌روزرسانی: <?= date('Y/m/d H:i') ?></footer>

</div>

<script>
// داده visuals هر کارت برای جابجایی رسانه اصلی
window.__vis = {};
<?php foreach ($products as $folder => $members):
      $visuals = array_values(array_filter($members, 'is_visual'));
      $arr = array_map(fn($m) => ['url' => $m['media_url'], 'video' => is_video($m)], $visuals);
      $hash = md5($folder); ?>
window.__vis['<?= $hash ?>'] = <?= json_encode($arr, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE) ?>;
<?php endforeach; ?>

function swapMedia(hash, idx, thEl) {
  const main = document.getElementById('main-' + hash);
  const item = (window.__vis[hash] || [])[idx];
  if (!item || !main) return;
  main.querySelectorAll('img, video').forEach(el => el.remove());
  let el;
  if (item.video) {
    el = document.createElement('video');
    el.src = item.url; el.controls = true; el.preload = 'metadata';
  } else {
    el = document.createElement('img');
    el.src = item.url; el.loading = 'lazy';
  }
  main.appendChild(el);
  thEl.parentElement.querySelectorAll('.th').forEach(t => t.classList.remove('active'));
  thEl.classList.add('active');
}
</script>
</body>
</html>
