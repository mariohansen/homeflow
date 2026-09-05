/* Element construction.

   Everything the client renders goes through here, and every piece of text is
   assigned as text, never parsed as markup. Device names come from vendor
   adapters and are untrusted input, so there is deliberately no path through
   this file that treats a string as HTML. */

const SVG_NS = "http://www.w3.org/2000/svg";

function apply(node, props) {
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null) continue;
    if (key === "class") node.setAttribute("class", value);
    else if (key === "text") node.textContent = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value);
  }
}

function adopt(node, children) {
  for (const child of [].concat(children)) {
    if (child) node.append(child);
  }
}

/** An HTML element. */
export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  // className is what the rendering checks read, and it is what a browser
  // reflects the class attribute into anyway.
  if (props.class !== undefined && props.class !== null) node.className = props.class;
  apply(node, { ...props, class: undefined });
  adopt(node, children);
  return node;
}

/** An SVG element: a different namespace, otherwise the same rules. */
export function svg(tag, props = {}, children = []) {
  const node = document.createElementNS(SVG_NS, tag);
  apply(node, props);
  adopt(node, children);
  return node;
}
