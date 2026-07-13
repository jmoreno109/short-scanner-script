def compute_short_score():

    score=0
    
    funding=-0.05    
    rsi=65

    # FUNDING
    if funding > 0.03 and rsi >= 70:
        score += 3  # euforia extrema
        print(f"Entro score += 3")

    elif funding > 0.01:
        score += 2
        print(f"Entro score += 2")

    elif funding > 0.002:
        score += 1
        print(f"Entro score += 1")

    elif funding < -0.02:
        score -= 2  # riesgo de short squeeze
        print(f"Entro score -= 2")


if __name__ == "__main__":
    compute_short_score()
