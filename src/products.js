// Dev snapshot of real SudsLab products (from the public storefront catalog).
// On Shopify, the section's Liquid renders live product data keyed by the same
// slot ids and this file is only the fallback. Bullets here are placeholder copy.

const CDN = 'https://cdn.shopify.com/s/files/1/0555/5470/3553/files/';
const STORE = 'https://www.suds-lab.com/products/';

const p = (slot, handle, title, price, image, bullets) => ({
  slot,
  handle,
  title,
  price,
  image: image.startsWith('https://') ? image : CDN + image,     // older listings keep their pictures under another folder
  url: STORE + handle,
  bullets,
});

export const PRODUCTS = [
  p('foam-cannon', 'f11-dual-chamber-foam-cannon-36oz', 'F11 Dual Chamber Foam Cannon, 36 oz', '49.99', 'F11_Main_Image_Dual_Chamber.png',
    ['Twin 18 fl oz chambers', 'Switch soaps with a twist', 'Adjustable foam ratio dial']),
  p('foam-master', 'f5-foam-master', 'F5 Foam Master', '29.99', 'SL-F5001-600-Main-with-Box.jpg',
    ['Hand-pump foam sprayer', 'Three interchangeable nozzles', 'No pressure washer needed']),
  p('f1-foam-cannon', 'f1-professional-foam-cannon', 'F1 Professional Foam Cannon', '28.31',
    'https://cdn.shopify.com/s/files/1/0555/5470/3553/products/SL-FC01-main-1-web.jpg',
    ['Attaches to your pressure washer', 'Adjustable foam control', 'Removes dirt and grime fast']),
  p('towels', 'suds-lab-mt-large-microfiber-drying-towels-18x24-2-pack-car-exterior-cleaning-and-detailing-cloth-safe-on-plastic-trim-glass-carbon-fiber-paint-etc-orange', 'MT Microfiber Drying Towels, 18 x 24 in. (2-Pack)', '24.98', 'SL-2MT01-800_2packoftowels_066a266a-ddd6-432a-989c-47e0ac40ab38.jpg',
    ['Plush microfiber', 'Safe on paint, glass and trim', '2-pack, 18 x 24 in.']),
  p('leather', 'lc-leather-deep-cleaning-leather-cleaner-spray-32-oz', 'LC Leather Deep Cleaning Spray, 32 oz.', '19.99', 'SL-LC032-276W-Main.jpg',
    ['Deep-cleans leather', 'Ready-to-use spray', '32 oz bottle']),
  p('ceramic-detail', 'cd-ceramic-detail-spray', 'CD Ceramic Detail Spray, 32 oz.', '15.99', 'SL-CD004-873W-CD.jpg',
    ['Ceramic-infused detailer', 'Quick gloss between washes', '32 oz bottle']),
  p('graphene', 'suds-lab-gc-graphene-spray-coating-sprayable-graphene-oxide-coating-for-cars-boats-rvs-motorcycles-for-extreme-gloss-depth-shine-and-protection-32-oz', 'GC Graphene Spray Coating, 32 oz.', '19.99', 'SL-GC032-151-GC.jpg',
    ['Sprayable graphene coating', 'Gloss, depth and protection', '32 oz bottle']),
  p('ceramic-wash', 'cp-ceramic-protective-wash', 'CP Ceramic Protective Wash, 32 oz.', '16.99', 'Bottle-Clear_32oz_CP.png',
    ['Wash and protect in one', 'Ceramic-infused soap', '32 oz bottle']),
  p('boar-brush', 'suds-lab-elite-bristle-boar-s-hair-car-detailing-brush', 'Elite Boar’s Hair Detailing Brush', '17.99', 'SL-RCB01-800-Main.jpg',
    ['Natural boar’s hair bristles', 'For vents, emblems and trim', 'Scratch-free ferrule']),
  p('wheel-brush', 'suds-lab-wheel-cleaning-brush-14', 'Mini Wheel Cleaning Brush, 14 in.', '11.99', 'SL-WCB01-800-Main_43330beb-6b32-40aa-8eec-b69ca21b66c1.jpg',
    ['14 in. reach', 'Gets between spokes', 'Non-slip grip']),
  p('mf-wheel-brush', 'wb-microfiber-wheel-cleaning-brush', 'WB Microfiber Wheel Cleaning Brush', '18.99', 'SL-MWB01-800-Main.jpg',
    ['Soft microfiber head', 'Safe on all wheel finishes', 'Long-reach handle']),
  p('tire-brush', 'sudslab-tire-brush-premium-wheel-cleaning-tire-brush-stiff-bristles-for-efficient-dirt-and-grime-removal-ergonomic-curved-handle-ideal-for-car-truck-and-suv-tires', 'HT Heavy-Duty Tire Brush', '7.99', 'SLHT001-800_Heavydutytirebrush.jpg',
    ['Stiff bristles', 'Ergonomic curved handle', 'For car, truck and SUV tires']),
  p('wash-mitt', 'em-extendable-wash-mitt', 'EM Extendable Wash Mitt, 58 in. Handle', '24.99', 'SL-AEM02-800-Main01.jpg',
    ['58 in. telescoping handle', 'Reaches roofs and trucks', 'Soft chenille mitt head']),
  p('iron-remover', 'suds-lab-fe-iron-particle-remover-fallout-rust-remover-spray-for-car-detailing-safe-on-car-paint-motorcycle-rv-boat-finishes-32-oz', 'FE Iron Particle Remover, 32 oz.', '19.99', 'Bottle-Clear_32oz_FE.png',
    ['Dissolves iron fallout', 'Safe on paint', '32 oz bottle']),
  p('bug-tar', 'suds-lab-bt-bug-tar-remover-all-purpose-exterior-cleaner-degreaser-wipe-away-bugs-on-plastic-rubber-metal-chrome-aluminum-windows', 'BT Bug & Tar Remover, 32 oz.', '19.99', 'SL-BT032-100-BT.jpg',
    ['Wipes away bugs and tar', 'Exterior-safe degreaser', '32 oz bottle']),
  p('tire-cleaner', 'suds-lab-tc-tire-and-rim-cleaner-acid-free-foaming-cleaning-solution-safe-for-all-finishes-home-car-detailing-wash', 'TC Tire and Rim Cleaner, 32 oz.', '19.99', 'SL-TC032-400-TC.jpg',
    ['Acid-free foaming formula', 'Safe for all finishes', '32 oz bottle']),
  p('tire-dressing', 't3-tire-trim-dressing', 'T3 Tire & Trim Dressing, 32 oz.', '19.99', 'Bottle-Clear_32oz_T3.png',
    ['Restores tires and trim', 'Spray-on application', '32 oz bottle']),
  p('xc-wash', 'xc-x-treme-conditions-wash-64oz', 'XC X-Treme Conditions Wash, 64 oz.', '24.99', 'SL-TW001-600W-Main-New.jpg',
    ['Heavy-duty car wash soap', 'Built for extreme grime', '64 oz jug']),
  p('blower', 'jd-jet-dry-car-drying-blower', 'JD Jet Dry Car Blower, 1.5 HP', '79.99', 'SL-CB001-Main-with-box.jpg',
    ['1.5 HP motor', 'Touch-free drying', 'Flexible hose with nozzle']),
];
