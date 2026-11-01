/** Hauteur maximale du contenu d'une table qui défile. Le navigateur plafonne la hauteur d'un élément (2^25 px sous
 * Chromium, divisés par le zoom) : au-delà d'un million de lignes environ, la fin de la table était inaccessible.
 * 8 millions de px restent sûrs jusqu'au zoom 400 %. */
export const MAX_SCROLL_HEIGHT = 8_000_000;

/** Déplacement de l'ascenseur au-delà duquel c'est un saut (ascenseur tiré, ligne cherchée), pas un pas de molette. */
const JUMP = 2000;

/** Relie la position de l'ascenseur à la position dans une table plus haute que ce que le navigateur sait afficher.
 *
 * Le contenu réel est borné à `MAX_SCROLL_HEIGHT` ; `offset` vaut position dans la table − position de l'ascenseur.
 * Un petit pas (molette, clavier) garde le décalage : la table avance ligne à ligne. Un saut place la table en
 * proportion de l'ascenseur ; en haut et en bas de l'ascenseur, on est au début et à la fin de la table. Sous le
 * plafond, le décalage reste nul. */
export class ScrollScale {
  total = 0; // hauteur de la table entière
  view = 0; // hauteur visible des lignes
  offset = 0;
  private last = 0; // dernière position connue de l'ascenseur

  /** Hauteur réelle du contenu. */
  get height(): number {
    return Math.min(this.total, MAX_SCROLL_HEIGHT);
  }

  /** Course de l'ascenseur. */
  private get room(): number {
    return Math.max(0, this.height - this.view);
  }

  private get ratio(): number {
    return this.room > 0 ? (this.total - this.view) / this.room : 1;
  }

  /** Position de l'ascenseur → position dans la table. */
  fromScroll(top: number): number {
    if (this.total <= MAX_SCROLL_HEIGHT || top <= 0) this.offset = 0;
    else if (top >= this.room - 1) this.offset = this.total - this.view - top;
    else if (Math.abs(top - this.last) > Math.max(this.view, JUMP)) this.offset = Math.round(top * this.ratio) - top;
    this.last = top;
    return top + this.offset;
  }

  /** Position voulue dans la table → position à donner à l'ascenseur ; le décalage suit. */
  toScroll(position: number): number {
    const target = Math.max(0, Math.min(position, this.total - this.view));
    if (this.total <= MAX_SCROLL_HEIGHT) {
      this.offset = 0;
      this.last = target;
      return target;
    }
    let top = target - this.offset;
    if (Math.abs(top - this.last) > Math.max(this.view, JUMP) || top < 1 || top > this.room - 2) {
      top = Math.min(this.room, Math.round(target / this.ratio));
      this.offset = top <= 0 ? 0 : top >= this.room - 1 ? this.total - this.view - top : target - top;
    }
    this.last = top;
    return top;
  }
}
